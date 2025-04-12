"""Peer implementation for P2P file sharing"""
from pathlib import Path
from magnet_utils import parse_magnet
from transfer import PieceManager, ConnectionManager
import sys
import os
import logging
import requests
import time
import json
import threading

# Thêm đường dẫn cha vào sys.path để import config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TRACKER_URL, DEFAULT_PEER_PORT, DEFAULT_PIECE_LENGTH

# Cấu hình logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("peer.log"),
        logging.StreamHandler()
    ]
)

# quản lý torrent, peer_id, repo_dir, active_downloads
class Peer:
    def __init__(self, peer_id=None, tracker_url=TRACKER_URL, listening_port=DEFAULT_PEER_PORT):
        """
        Khởi tạo Peer
        
        Parameters:
            peer_id (str): ID của peer (nếu không có sẽ tự động tạo)
            tracker_url (str): URL của tracker
            listening_port (int): Cổng để lắng nghe kết nối từ các peer khác
        """
        # Tạo peer_id nếu không có
        self.peer_id = peer_id or self._generate_peer_id()
        self.tracker_url = tracker_url
        self.listening_port = listening_port
        self.torrents = {}  # nơi lưu trữ torrent thông tin
        
        # lưu trữ torrent vào thư mục downloads
        self.repo_dir = Path("downloads")
        os.makedirs(self.repo_dir, exist_ok=True)
        
        # theo dõi các download đang hoạt động
        self.active_downloads = {}  # {info_hash: ConnectionManager}
        
        # Khởi tạo lock để đồng bộ các thread
        self.lock = threading.Lock()
        
    def _generate_peer_id(self):
        """Tạo một ID cho peer"""
        # Tạo ID ngẫu nhiên với định dạng -ST0001-xxxxxxxxxxxx
        # xxxxxxxxxxxx là 12 ký tự ngẫu nhiên (chữ và số)
        import random
        import string
        random_chars = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(12))
        return f"-ST0001-{random_chars}"
    
    def add_torrent_from_magnet(self, magnet_url):
        """
        Thêm torrent từ magnet URL
        
        Parameters:
            magnet_url (str): Magnet URL của torrent
            
        Returns:
            str: Info hash của torrent đã thêm hoặc None nếu không thành công
        """
        try:
            # Phân tích magnet URL để lấy info_hash
            info = parse_magnet(magnet_url)
            info_hash = info['info_hash']
            
            # Kiểm tra xem torrent đã tồn tại chưa
            with self.lock:
                if info_hash in self.torrents:
                    logging.info(f"Torrent {info_hash} đã tồn tại")
                    if self.torrents[info_hash]['status'] == 'paused':
                        return self.resume_torrent(info_hash)
                    return info_hash
                
            # Lấy metainfo từ tracker
            metainfo = self._fetch_metainfo(info_hash)
            if not metainfo:
                logging.error(f"Không thể lấy metainfo cho {info_hash}") 
                return None
                
            # Tạo thư mục cho torrent
            torrent_dir = self.repo_dir / info_hash
            os.makedirs(torrent_dir, exist_ok=True)
            
            # Tạo PieceManager cho torrent
            piece_manager = PieceManager(
                info_hash=info_hash,
                piece_length=metainfo.get('piece_length', DEFAULT_PIECE_LENGTH),
                piece_count=metainfo.get('piece_count', 0),
                files=metainfo.get('files', []),
                repo_dir=self.repo_dir
            )
            
            # Thiết lập piece hashes nếu có
            if 'pieces' in metainfo:
                piece_manager.set_piece_hashes(metainfo['pieces'])
            
            # Bắt đầu giao tiếp với tracker để lấy danh sách peers
            peers = self._announce_to_tracker(info_hash, piece_manager, event="started")
            
            # Lưu thông tin torrent vào danh sách torrents
            with self.lock:
                self.torrents[info_hash] = {
                    'metainfo': metainfo,
                    'piece_manager': piece_manager,
                    'status': 'started',
                    'added_time': time.time(),
                    'peers': []
                }
            
            # Bắt đầu kết nối với peers
            self._start_connections(info_hash, piece_manager, peers)
            
            return info_hash
            
        except Exception as e:
            logging.error(f"Lỗi khi thêm torrent từ magnet URL: {e}")
            return None
    
    def add_multiple_torrents(self, magnet_urls):
        """
        Thêm nhiều torrent từ danh sách magnet URLs (hỗ trợ MDDT)
        
        Parameters:
            magnet_urls (list): Danh sách magnet URLs
            
        Returns:
            list: Danh sách info_hashes đã thêm thành công
        """
        info_hashes = []
        
        # Tạo các thread riêng để thêm torrent song song
        threads = []
        for magnet_url in magnet_urls:
            thread = threading.Thread(
                target=lambda url, result_list: result_list.append(self.add_torrent_from_magnet(url)),
                args=(magnet_url, info_hashes)
            )
            threads.append(thread)
            thread.start()
        
        # Chờ tất cả thread hoàn thành
        for thread in threads:
            thread.join()
                
        return [h for h in info_hashes if h is not None]

    def pause_torrent(self, info_hash):
        """
        Tạm dừng tải/chia sẻ torrent
        
        Parameters:
            info_hash (str): Info hash của torrent cần tạm dừng
            
        Returns:
            bool: True nếu tạm dừng thành công, False nếu không
        """
        try:
            if info_hash not in self.torrents:
                logging.warning(f"Torrent {info_hash} không tồn tại")
                return False
                
            with self.lock:
                # Kiểm tra xem torrent đã tạm dừng chưa
                if self.torrents[info_hash]['status'] == 'paused':
                    return True
                    
                # Gửi sự kiện 'stopped' đến tracker
                torrent = self.torrents[info_hash]
                piece_manager = torrent['piece_manager']
                
                self._announce_to_tracker(
                    info_hash, 
                    piece_manager, 
                    event="stopped"
                )
                
                # Dừng connection manager
                if info_hash in self.active_downloads:
                    self.active_downloads[info_hash].stop()
                    del self.active_downloads[info_hash]
                
                # Cập nhật trạng thái
                self.torrents[info_hash]['status'] = 'paused'
                
            return True
            
        except Exception as e:
            logging.error(f"Lỗi khi tạm dừng torrent {info_hash}: {e}")
            return False
    
    def resume_torrent(self, info_hash):
        """
        Tiếp tục tải/chia sẻ torrent đã tạm dừng
        
        Parameters:
            info_hash (str): Info hash của torrent cần tiếp tục
            
        Returns:
            bool: True nếu tiếp tục thành công, False nếu không
        """
        try:
            if info_hash not in self.torrents:
                logging.warning(f"Torrent {info_hash} không tồn tại")
                return False
                
            with self.lock:
                # Kiểm tra xem torrent đã chạy chưa
                if self.torrents[info_hash]['status'] == 'started':
                    return True
                    
                # Khởi động lại quá trình tải xuống
                torrent = self.torrents[info_hash]
                piece_manager = torrent['piece_manager']
                
                # Gửi sự kiện 'started' đến tracker
                peers = self._announce_to_tracker(
                    info_hash, 
                    piece_manager, 
                    event="started"
                )
                
                # Bắt đầu kết nối với peers
                self._start_connections(info_hash, piece_manager, peers)
                
                # Cập nhật trạng thái
                self.torrents[info_hash]['status'] = 'started'
                
            return True
            
        except Exception as e:
            logging.error(f"Lỗi khi tiếp tục torrent {info_hash}: {e}")
            return False
    
    def stop_all(self):
        """
        Dừng tất cả các torrent đang hoạt động
        
        Returns:
            bool: True nếu dừng thành công, False nếu có lỗi
        """
        try:
            with self.lock:
                # Lưu danh sách info_hash để tránh lỗi khi duyệt dict
                info_hashes = list(self.torrents.keys())
                
                for info_hash in info_hashes:
                    self.pause_torrent(info_hash)
                    
            return True
            
        except Exception as e:
            logging.error(f"Lỗi khi dừng tất cả torrent: {e}")
            return False
    
    def remove_torrent(self, info_hash, delete_files=False):
        """
        Xóa torrent khỏi danh sách theo dõi
        
        Parameters:
            info_hash (str): Info hash của torrent cần xóa
            delete_files (bool): Nếu True, xóa cả các file đã tải xuống
            
        Returns:
            bool: True nếu xóa thành công, False nếu không
        """
        try:
            if info_hash not in self.torrents:
                return False
                
            # Dừng và thông báo cho tracker
            self.pause_torrent(info_hash)
            
            with self.lock:
                # Xóa khỏi danh sách theo dõi
                if info_hash in self.torrents:
                    del self.torrents[info_hash]
                
                # Xóa file nếu cần
                if delete_files:
                    torrent_dir = self.repo_dir / info_hash
                    if os.path.exists(torrent_dir):
                        import shutil
                        shutil.rmtree(torrent_dir)
                        
            return True
            
        except Exception as e:
            logging.error(f"Lỗi khi xóa torrent {info_hash}: {e}")
            return False
    
    def _fetch_metainfo(self, info_hash):
        """
        Lấy metainfo từ tracker
        
        Parameters:
            info_hash (str): Info hash của torrent
            
        Returns:
            dict: Dữ liệu metainfo nếu tìm thấy, ngược lại trả về None
        """
        try:
            response = requests.get(
                f"{self.tracker_url}/metainfo",
                params={'info_hash': info_hash}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"Không thể lấy metainfo: {response.text}")
                return None
                
        except Exception as e:
            logging.error(f"Lỗi khi lấy metainfo: {e}")
            return None
    
    def _announce_to_tracker(self, info_hash, piece_manager, event="started"):
        """
        Gửi yêu cầu announce đến tracker để lấy danh sách peers
        
        Parameters:
            info_hash (str): Info hash của torrent
            piece_manager (PieceManager): Quản lý các phần của torrent này
            event (str): Loại sự kiện ('started', 'completed', 'stopped')
            
        Returns:
            list: Danh sách peers từ tracker
        """
        try:
            # Lấy thông tin IP và port của peer
            # Trong môi trường thực tế, cần lấy IP public
            ip = "127.0.0.1"
            port = self.listening_port
            
            # Tạo payload cho yêu cầu announce
            payload = {
                "peer_id": self.peer_id,
                "info_hash": info_hash,
                "ip": ip,
                "port": port,
                "uploaded": piece_manager.bytes_uploaded,
                "downloaded": piece_manager.bytes_downloaded,
                "left": piece_manager.bytes_left,
                "event": event,
                "compact": 1
            }
            
            # Gửi yêu cầu đến tracker
            response = requests.post(
                f"{self.tracker_url}/announce",
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Kiểm tra xem có cảnh báo gì không
                if data.get("warning"):
                    logging.warning(f"Cảnh báo từ tracker: {data['warning']}")
                
                # Trả về danh sách peers từ phản hồi
                return data.get("peers", [])
            else:
                logging.error(f"Announce thất bại: {response.text}")
                return []
                
        except Exception as e:
            logging.error(f"Lỗi khi gửi announce đến tracker: {e}")
            return []
    
    def _start_connections(self, info_hash, piece_manager, peers):
        """
        Bắt đầu kết nối đến các peers từ tracker
        
        Parameters:
            info_hash (str): Info hash của torrent
            piece_manager (PieceManager): Quản lý các phần của torrent
            peers (list): Danh sách peers từ tracker
        """
        try:
            # Tạo ConnectionManager để quản lý kết nối
            connection_manager = ConnectionManager(
                info_hash=info_hash,
                piece_manager=piece_manager,
                peer_id=self.peer_id,
                announce_callback=lambda: self._announce_to_tracker(info_hash, piece_manager)
            )
            
            # Thêm các peer vào connection manager
            for peer in peers:
                connection_manager.add_peer(peer)
                
            # Lưu vào active_downloads
            with self.lock:
                self.active_downloads[info_hash] = connection_manager
                
            # Bắt đầu kết nối và tải xuống
            connection_manager.start()
            
            logging.info(f"Đã bắt đầu kết nối với {len(peers)} peers cho torrent {info_hash}")
            
        except Exception as e:
            logging.error(f"Lỗi khi bắt đầu kết nối đến peers: {e}")
    
    def check_all_torrents(self):
        """
        Kiểm tra và cập nhật trạng thái tất cả các torrent
        Gửi yêu cầu announce 'completed' khi tải xong
        """
        try:
            with self.lock:
                for info_hash, torrent in self.torrents.items():
                    piece_manager = torrent['piece_manager']
                    
                    # Kiểm tra nếu torrent đã hoàn thành nhưng chưa báo cho tracker
                    if piece_manager.progress >= 1.0 and torrent['status'] != 'completed':
                        # Gửi sự kiện 'completed' cho tracker
                        self._announce_to_tracker(info_hash, piece_manager, event="completed")
                        
                        # Cập nhật trạng thái
                        torrent['status'] = 'completed'
                        logging.info(f"Torrent {info_hash} đã tải xuống hoàn tất")
                        
                    # Nếu torrent đang tạm dừng, bỏ qua
                    if torrent['status'] == 'paused':
                        continue
                    
        except Exception as e:
            logging.error(f"Lỗi khi kiểm tra torrent: {e}")
    
    def get_status(self, info_hash=None):
        """
        Lấy trạng thái của torrent hoặc tất cả các torrent
        
        Parameters:
            info_hash (str, optional): Info hash của torrent cụ thể
            
        Returns:
            dict: Trạng thái của torrent hoặc tất cả các torrent
        """
        try:
            with self.lock:
                if info_hash:
                    if info_hash not in self.torrents:
                        return {"error": "Torrent không tồn tại"}
                        
                    torrent = self.torrents[info_hash]
                    piece_manager = torrent['piece_manager']
                    
                    # Tính toán tốc độ tải xuống/tải lên
                    download_speed = 0  # bytes/s
                    upload_speed = 0    # bytes/s
                    
                    if info_hash in self.active_downloads:
                        conn_manager = self.active_downloads[info_hash]
                        for peer_id, peer in conn_manager.peers.items():
                            if peer['handler'] and peer['connected']:
                                download_speed += peer['handler'].bytes_downloaded
                                upload_speed += peer['handler'].bytes_uploaded
                    
                    return {
                        "info_hash": info_hash,
                        "name": torrent['metainfo'].get('name', 'Unknown'),
                        "progress": piece_manager.progress * 100,
                        "downloaded": piece_manager.bytes_downloaded,
                        "uploaded": piece_manager.bytes_uploaded,
                        "left": piece_manager.bytes_left,
                        "status": torrent['status'],
                        "download_speed": download_speed,
                        "upload_speed": upload_speed,
                        "files": torrent['metainfo'].get('files', []),
                        "piece_count": piece_manager.piece_count,
                        "piece_length": piece_manager.piece_length
                    }
                else:
                    # Trả về trạng thái của tất cả các torrent
                    result = {}
                    for hash, t in self.torrents.items():
                        result[hash] = {
                            "name": t['metainfo'].get('name', 'Unknown'),
                            "progress": t['piece_manager'].progress * 100,
                            "status": t['status'],
                            "downloaded": t['piece_manager'].bytes_downloaded,
                            "uploaded": t['piece_manager'].bytes_uploaded,
                            "left": t['piece_manager'].bytes_left
                        }
                    return result
        
        except Exception as e:
            logging.error(f"Lỗi khi lấy trạng thái: {e}")
            return {"error": str(e)}
    
    def get_peer_stats(self, info_hash):
        """
        Lấy thống kê về các peer đang kết nối
        
        Parameters:
            info_hash (str): Info hash của torrent
            
        Returns:
            list: Danh sách thông tin về các peer
        """
        try:
            if info_hash not in self.active_downloads:
                return []
            
            stats = []
            conn_manager = self.active_downloads[info_hash]
            
            with self.lock:
                for peer_id, peer in conn_manager.peers.items():
                    if not peer['handler']:
                        continue
                    
                    handler = peer['handler']
                    stats.append({
                        "peer_id": peer_id,
                        "ip": peer['info'].get('ip'),
                        "port": peer['info'].get('port'),
                        "connected": peer['connected'],
                        "downloaded": handler.bytes_downloaded,
                        "uploaded": handler.bytes_uploaded,
                        "choking_us": handler.peer_choking,
                        "we_choking": handler.am_choking,
                        "pieces": sum(1 for p in handler.peer_bitfield if p),
                        "last_active": time.time() - handler.last_activity
                    })
            
            return stats
            
        except Exception as e:
            logging.error(f"Lỗi khi lấy thống kê peer: {e}")
            return []

if __name__ == "__main__":
    # Khởi tạo peer
    peer = Peer()
    
    # Thêm torrent từ magnet URL
    magnet_url = "magnet:?xt=urn:btih:abc123&dn=sample_file&tr=http://localhost:8000"
    info_hash = peer.add_torrent_from_magnet(magnet_url)
    
    if info_hash:
        print(f"Đã thêm torrent với info_hash: {info_hash}")
        
        # Chạy vòng lặp để theo dõi trạng thái torrent
        try:
            while True:
                print("\nLệnh: status, quit")
                cmd = input("> ").strip().lower()
                
                if cmd == "status":
                    status = peer.get_status()
                    print(json.dumps(status, indent=2))
                elif cmd == "quit":
                    print("Đang thoát...")
                    break
                else:
                    print("Lệnh không hợp lệ")
                    
        except KeyboardInterrupt:
            print("\nĐang thoát...")
            
        # Kết thúc tất cả các kết nối đang hoạt động
        peer.stop_all()
    else:
        print("Không thể thêm torrent.")