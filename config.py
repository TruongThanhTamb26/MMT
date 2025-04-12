"""
Cấu hình hệ thống P2P

Sửa file này để thay đổi cấu hình khi chuyển sang máy khác.
Không cần sửa code trong các file khác.
"""

# Địa chỉ IP máy đang chạy tracker server
# Trong môi trường thực tế, đây là địa chỉ IP public hoặc domain name
# Khi chạy trên cùng một máy, có thể để là localhost
TRACKER_HOST = "10.28.128.187"  # Thay đổi IP này thành địa chỉ IP của máy bạn trên mạng LAN

# Cổng của tracker server
TRACKER_PORT = 8000

# Cổng của web server
WEB_SERVER_PORT = 5000

# Cổng mặc định để lắng nghe kết nối từ các peer khác
DEFAULT_PEER_PORT = 6881

# URL đầy đủ của tracker
TRACKER_URL = f"http://{TRACKER_HOST}:{TRACKER_PORT}"

# Thư mục lưu trữ tệp tải về
DOWNLOAD_DIR = "downloads"

# Thư mục lưu metainfo
METAINFO_DIR = "metainfo"

# Kích thước piece mặc định (512 KB)
DEFAULT_PIECE_LENGTH = 512 * 1024