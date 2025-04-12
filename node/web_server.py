"""Web server for P2P node interface"""
import os
import json
import logging
import threading
import tempfile
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory
from pathlib import Path
from werkzeug.utils import secure_filename

# Cấu hình logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("web_server.log"),
        logging.StreamHandler()
    ]
)

# Import Peer class
from peer import Peer
from magnet_utils import create_magnet
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from create_torrent import create_metainfo, save_metainfo, create_magnet_link
from config import TRACKER_URL, WEB_SERVER_PORT, DEFAULT_PEER_PORT

# Khởi tạo Flask app
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB limit
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Khởi tạo peer singleton
peer = None
def init_peer(tracker_url=TRACKER_URL, port=DEFAULT_PEER_PORT):
    global peer
    if peer is None:
        peer = Peer(tracker_url=tracker_url, listening_port=port)
        # Bắt đầu thread kiểm tra trạng thái torrent
        threading.Thread(target=status_monitor_thread, daemon=True).start()
    return peer

def status_monitor_thread():
    """Thread theo dõi trạng thái torrent tự động"""
    import time
    while True:
        try:
            if peer:
                peer.check_all_torrents()
        except Exception as e:
            logging.error(f"Error in status monitor thread: {e}")
        time.sleep(5)  # Kiểm tra mỗi 5 giây

@app.route("/")
def index():
    """Trang chủ hiển thị danh sách torrent và thống kê"""
    init_peer()
    status = peer.get_status()
    return render_template("index.html", status=status, tracker_url=peer.tracker_url, peer_id=peer.peer_id, torrents=status)

@app.route("/api/status")
def api_status():
    """API endpoint trả về trạng thái torrent dưới dạng JSON"""
    init_peer()
    info_hash = request.args.get('info_hash')
    status = peer.get_status(info_hash)
    
    # Thêm thông tin MDDT nếu là thông tin chi tiết của một torrent
    if info_hash and isinstance(status, dict) and 'info_hash' in status:
        # Thêm số lượng peers đang kết nối
        peers = peer.get_peer_stats(info_hash)
        status['connected_peers'] = len([p for p in peers if p.get('connected', False)])
        
        # Tính toán tốc độ tải xuống/lên hiện tại
        download_speed = 0
        upload_speed = 0
        for p in peers:
            if p.get('connected', False):
                download_speed += getattr(p, 'download_speed', 0)
                upload_speed += getattr(p, 'upload_speed', 0)
        
        status['download_speed'] = download_speed
        status['upload_speed'] = upload_speed
        
        # Tính hiệu suất MDDT (tỷ lệ tải xuống thực tế so với lý thuyết)
        connected_peers = status['connected_peers']
        if connected_peers > 1:
            # Tính tỉ lệ hiệu quả dựa trên số peers kết nối
            theoretical_max = connected_peers * status.get('piece_length', 0) / 10  # Giả sử tải 1/10 piece mỗi giây
            if theoretical_max > 0:
                mddt_efficiency = min(100, (download_speed / theoretical_max) * 100)
                status['mddt_efficiency'] = round(mddt_efficiency, 1)
            else:
                status['mddt_efficiency'] = 0
        else:
            status['mddt_efficiency'] = 0
            
        # Tính tổng kích thước
        status['total_size'] = sum(f.get('length', 0) for f in status.get('files', []))
    
    return jsonify(status)

@app.route("/get_status")
def get_status():
    """API endpoint trả về trạng thái tất cả các torrent"""
    init_peer()
    status = peer.get_status()
    return jsonify(status)

@app.route("/api/peers")
def api_peers():
    """API endpoint trả về thông tin các peer đang kết nối"""
    init_peer()
    info_hash = request.args.get('info_hash')
    if not info_hash:
        return jsonify({"error": "Missing info_hash parameter"}), 400
        
    peers = peer.get_peer_stats(info_hash)
    return jsonify(peers)

@app.route("/add", methods=["GET", "POST"])
def add_torrent():
    """Trang thêm torrent mới"""
    init_peer()
    
    if request.method == "POST":
        # Xử lý thêm torrent
        magnet_url = request.form.get("magnet_url")
        if magnet_url:
            info_hash = peer.add_torrent_from_magnet(magnet_url)
            if info_hash:
                return redirect(url_for("index"))
            else:
                return render_template("add_torrent.html", error="Failed to add torrent")
        else:
            return render_template("add_torrent.html", error="Invalid magnet URL")
    
    return render_template("add_torrent.html")

@app.route("/create_torrent", methods=["GET", "POST"])
def create_torrent_page():
    """Trang tạo torrent mới"""
    if request.method == "POST":
        try:
            # Xử lý tạo torrent từ các file được tải lên
            if 'files' not in request.files:
                return render_template("create_torrent.html", error="No files selected")
                
            files = request.files.getlist('files')
            if not files or not files[0].filename:
                return render_template("create_torrent.html", error="No files selected")
            
            # Lưu các file tạm thời
            temp_files = []
            for file in files:
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                temp_files.append(file_path)
            
            # Lấy các tham số khác
            name = request.form.get('name')
            if not name and len(temp_files) == 1:
                name = os.path.basename(temp_files[0])
                
            piece_length = int(request.form.get('piece_length', 512*1024))
            tracker_url = request.form.get('tracker_url', TRACKER_URL)
            
            # Tạo metainfo
            info_hash, metainfo = create_metainfo(
                temp_files,
                piece_length=piece_length,
                tracker_url=tracker_url,
                name=name
            )
            
            # Lưu metainfo
            tracker_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tracker', 'metainfo')
            save_metainfo(metainfo, tracker_dir)
            
            # Tạo magnet link
            magnet_link = create_magnet_link(info_hash, name, tracker_url)
            
            return render_template(
                "create_torrent.html", 
                success=f"Torrent created successfully with info_hash: {info_hash}",
                magnet_link=magnet_link
            )
            
        except Exception as e:
            logging.error(f"Error creating torrent: {e}")
            return render_template("create_torrent.html", error=str(e))
    
    return render_template("create_torrent.html")

@app.route("/upload", methods=["POST"])
def upload_file():
    """API endpoint để upload file để tạo torrent"""
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400
            
        file = request.files["file"]
        
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400
            
        # Lưu file vào thư mục uploads
        upload_dir = Path("uploads")
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = upload_dir / secure_filename(file.filename)
        file.save(file_path)
        
        return jsonify({
            "success": True,
            "file_path": str(file_path)
        })
        
    except Exception as e:
        logging.error(f"Error uploading file: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/pause/<info_hash>")
def pause_torrent(info_hash):
    """API endpoint để tạm dừng torrent"""
    init_peer()
    peer.pause_torrent(info_hash)
    return jsonify({"status": "paused"})

@app.route("/api/resume/<info_hash>")
def resume_torrent(info_hash):
    """API endpoint để tiếp tục torrent bị tạm dừng"""
    init_peer()
    result = peer.resume_torrent(info_hash)
    return jsonify({"status": "resumed" if result else "failed"})

@app.route("/details/<info_hash>")
def torrent_details(info_hash):
    """Trang chi tiết torrent"""
    init_peer()
    status = peer.get_status(info_hash)
    peers = peer.get_peer_stats(info_hash)
    
    return render_template(
        "details.html", 
        status=status, 
        peers=peers,
        info_hash=info_hash
    )

@app.template_filter('format_size')
def format_size(size_bytes):
    """Helper để định dạng kích thước file (bytes -> KB, MB, GB)"""
    if size_bytes == 0:
        return "0B"
        
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024
        i += 1
    
    return f"{size_bytes:.2f} {size_names[i]}"

@app.template_filter('format_progress')
def format_progress(progress):
    """Helper để định dạng tiến độ tải xuống"""
    return f"{progress:.1f}%"

@app.template_filter('truncate')
def truncate(text, length=10):
    """Helper để cắt ngắn văn bản dài"""
    if len(text) <= length:
        return text
    return text[:length] + '...'

@app.template_filter('format_speed')
def format_speed(bytes_per_sec):
    """Helper để định dạng tốc độ (B/s -> KB/s, MB/s)"""
    if bytes_per_sec == 0 or bytes_per_sec is None:
        return "0 B/s"
        
    size_names = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    speed_value = float(bytes_per_sec)
    while speed_value >= 1024 and i < len(size_names) - 1:
        speed_value /= 1024
        i += 1
    
    return f"{speed_value:.2f} {size_names[i]}"

def shutdown_server():
    """Hàm để dừng server Flask một cách an toàn"""
    import signal
    signal.raise_signal(signal.SIGINT)

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """API endpoint để tắt server"""
    try:
        # Dừng tất cả các torrent đang hoạt động
        if peer:
            peer.stop_all()
            
        # Dừng server
        threading.Thread(target=shutdown_server).start()
        return jsonify({"status": "shutting_down"})
    except Exception as e:
        logging.error(f"Error shutting down: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Phục vụ các file đã upload"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.errorhandler(413)
def request_entity_too_large(error):
    """Xử lý lỗi file quá lớn"""
    return render_template("create_torrent.html", error="File upload too large. Maximum size is 1GB."), 413

if __name__ == "__main__":
    # Khởi tạo peer
    init_peer()
    
    # Khởi động Flask server
    app.run(host="0.0.0.0", port=WEB_SERVER_PORT, debug=True)