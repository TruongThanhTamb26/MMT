""" Xử lí các request của Peers"""
import logging
from flask import Flask, request, jsonify, render_template  # flask: Thư viện cho giao thức HTTP 
                                                            # request: hàm lấy các yêu cầu gửi đến TRACKER
                                                            # jsonify: hàm để phản hồi file JSON cho các PEER
from tracker_utils import (validate_announce_payload)
from state_manager import (manage_peer, get_stats, peer_registry)
from metainfo_manager import load_metainfo

# Cấu hình logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tracker.log"),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)

@app.route("/", methods=['GET'])
def index():
    """Trang chủ hiển thị thống kê của tracker"""
    stats = get_stats()
    return jsonify(stats)

@app.route("/announce", methods=['POST'])
def announce():
    """
    Xử lý request announce từ các peer
    
    Các loại event:
    - started: Peer bắt đầu tải torrent
    - completed: Peer đã tải xong toàn bộ torrent
    - stopped: Peer ngừng tải torrent
    """
    logging.debug("Received announce request")
    
    # Kiểm tra file JSON có đúng chuẩn chưa
    try:
        request_data = request.get_json(force=True)
        logging.debug(f"Request data: {request_data}")
    except Exception as e:
        logging.error(f"Error parsing JSON: {e}")
        return jsonify({
            "failure_reason": "Invalid JSON format",
            "warning": None,
            "peers": [],
            "tracker_id": None
        }), 400
    
    # Kiểm tra failure
    failure_reason, warning = validate_announce_payload(request_data)
    if failure_reason:
        logging.warning(f"Invalid request: {failure_reason}")
        return jsonify({
            "failure_reason": failure_reason,
            "warning": None,
            "peers": [],
            "tracker_id": None
        }), 400

    # Trả về phản hồi của TRACKER
    peer_response = manage_peer(request_data)
    peer_response["warning"] = warning
    
    logging.debug(f"Response: {peer_response}")

    # Phản hồi về PEER file JSON chứa danh sách các PEER
    return jsonify(peer_response), 200

@app.route("/scrape", methods=['GET'])
def scrape(): 
    """
    Cung cấp thông tin tổng quan về các torrent được theo dõi
    
    """
    info_hash = request.args.get('info_hash')
    stats = get_stats()
    
    if info_hash:
        # Trả về thông tin cụ thể cho info_hash
        return jsonify({
            "files": {
                info_hash: {
                    "complete": sum(1 for p in peer_registry.get(info_hash, []) if p["left"] == 0),
                    "incomplete": sum(1 for p in peer_registry.get(info_hash, []) if p["left"] > 0),
                    "downloaded": 0  # Cần theo dõi thêm số lần tải xuống hoàn chỉnh
                }
            }
        })
    else:
        # Trả về thông tin tổng quan
        return jsonify({
            "files": stats
        })
    
@app.route("/metainfo", methods=['GET'])
def get_metainfo():
    """
    Cung cấp thông tin metainfo cho một torrent cụ thể
    """
    info_hash = request.args.get('info_hash')
    
    if not info_hash:
        return jsonify({
            "failure_reason": "Missing info_hash parameter"
        }), 400
    
    metainfo = load_metainfo(info_hash)
    
    if not metainfo:
        return jsonify({
            "failure_reason": "Metainfo not found"
        }), 404
    
    return jsonify(metainfo), 200


@app.route("/stats", methods=['GET'])
def stats_page():
    """
    Provides a visual interface showing tracker statistics
    """
    stats = get_stats()
    torrents = {}
    
    # Get details for each torrent
    for info_hash in peer_registry:
        metainfo = load_metainfo(info_hash)
        if metainfo:
            seeders = sum(1 for p in peer_registry[info_hash] if p["left"] == 0)
            leechers = sum(1 for p in peer_registry[info_hash] if p["left"] > 0)
            
            torrents[info_hash] = {
                "name": metainfo.get("name", "Unknown"),
                "seeders": seeders,
                "leechers": leechers,
                "size": sum(f['length'] for f in metainfo.get('files', []))
            }
    
    # Render HTML template
    return render_template(
        'stats.html', 
        stats=stats,
        torrents=torrents
    )

if __name__ == "__main__":
    logging.info("Tracker server starting on port 8000...")
    app.run(host="0.0.0.0", port=8000)

