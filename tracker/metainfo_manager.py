"""Xử lý metainfo cho torrent"""
import os
import json
import logging
import hashlib
from pathlib import Path

METAINFO_DIR = Path("metainfo")

def load_metainfo(info_hash):
    """
    Tải metainfo từ thư mục metainfo dựa trên info_hash
    
    Parameters:
        info_hash (str): Mã băm của thông tin torrent
        
    Returns:
        dict: Dữ liệu metainfo nếu tìm thấy, ngược lại trả về None
    """
    try:
        # Tìm kiếm trong thư mục metainfo
        for file in METAINFO_DIR.glob("*.torrent.json"):
            with open(file, 'r') as f:
                data = json.load(f)
                # Kiểm tra xem info_hash có khớp với thông tin trong file không
                if data.get("info_hash") == info_hash:
                    return data
        
        logging.warning(f"No metainfo found for hash: {info_hash}")
        return None
    
    except Exception as e:
        logging.error(f"Error loading metainfo: {e}")
        return None

def create_metainfo(file_paths, piece_length=512*1024, tracker_url="http://localhost:8000", name=None):
    """
    Tạo metainfo cho một hoặc nhiều file torrent và lưu vào thư mục metainfo
    
    Parameters:
        file_paths (list): Danh sách đường dẫn đến các file cần tạo metainfo
        piece_length (int): Kích thước mỗi phần (mặc định 512KB)
        tracker_url (str): URL của tracker
        name (str): Tên của torrent, nếu không cung cấp sẽ sử dụng tên thư mục chứa file
        
    Returns:
        tuple: (info_hash, metainfo) - info_hash là mã băm của thông tin torrent, metainfo là dữ liệu metainfo
    """
    if isinstance(file_paths, str):
        file_paths = [file_paths]  # Chuyển đổi string thành list nếu chỉ có một file
        
    # Kiểm tra tất cả các file tồn tại
    for file_path in file_paths:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
    
    # Tính toán tổng kích thước các file
    file_infos = []
    total_size = 0
    
    for file_path in file_paths:
        path = Path(file_path)
        file_size = path.stat().st_size
        file_infos.append({
            "path": str(path.name),
            "length": file_size
        })
        total_size += file_size
    
    # Tính toán số lượng phần
    piece_count = (total_size + piece_length - 1) // piece_length
    
    # Xác định tên torrent
    if name is None:
        if len(file_paths) == 1:
            name = Path(file_paths[0]).name
        else:
            # Sử dụng tên thư mục chứa nếu các file cùng thư mục
            common_parent = Path(file_paths[0]).parent
            name = common_parent.name + "-torrent"
    
    # Tạo metainfo
    metainfo = {
        "name": name,
        "piece_length": piece_length,
        "piece_count": piece_count,
        "files": file_infos,
        "tracker": tracker_url
    }
    
    # Tính toán info_hash
    # Sử dụng SHA1 để tạo info_hash
    info_hash = hashlib.sha1(json.dumps(metainfo, sort_keys=True).encode()).hexdigest()
    metainfo["info_hash"] = info_hash
    
    # Lưu metainfo vào file JSON
    # Tạo thư mục nếu chưa tồn tại
    output_path = METAINFO_DIR / f"{name}.torrent.json"
    os.makedirs(METAINFO_DIR, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(metainfo, f, indent=2)  # indent=2 để dễ đọc hơn
    
    return info_hash, metainfo

def calculate_pieces_hash(file_paths, piece_length=512*1024):
    """
    Tính toán hash cho từng piece trong torrent
    
    Parameters:
        file_paths (list): Danh sách đường dẫn đến các file
        piece_length (int): Kích thước mỗi phần
        
    Returns:
        list: Danh sách các hash của từng piece
    """
    if isinstance(file_paths, str):
        file_paths = [file_paths]
    
    piece_hashes = []
    current_piece = b""
    
    # Đọc từng file và tính toán hash cho từng piece
    for file_path in file_paths:
        with open(file_path, 'rb') as f:
            while True:
                data = f.read(piece_length - len(current_piece))
                if not data:
                    break
                
                current_piece += data
                
                if len(current_piece) == piece_length:
                    # Tính hash cho piece đủ kích thước
                    piece_hash = hashlib.sha1(current_piece).hexdigest()
                    piece_hashes.append(piece_hash)
                    current_piece = b""
    
    # Xử lý piece cuối cùng nếu chưa đủ kích thước
    if current_piece:
        piece_hash = hashlib.sha1(current_piece).hexdigest()
        piece_hashes.append(piece_hash)
    
    return piece_hashes

def update_metainfo_with_pieces(info_hash, piece_hashes):
    """
    Cập nhật metainfo với hash của các piece
    
    Parameters:
        info_hash (str): Hash của metainfo cần cập nhật
        piece_hashes (list): Danh sách các hash của từng piece
        
    Returns:
        bool: True nếu cập nhật thành công, False nếu không tìm thấy metainfo
    """
    try:
        # Tìm file metainfo theo info_hash
        for file_path in METAINFO_DIR.glob("*.torrent.json"):
            with open(file_path, 'r') as f:
                data = json.load(f)
                
                if data.get("info_hash") == info_hash:
                    # Cập nhật danh sách piece hashes
                    data["pieces"] = piece_hashes
                    
                    # Ghi lại vào file
                    with open(file_path, 'w') as f_write:
                        json.dump(data, f_write, indent=2)
                    
                    return True
        
        return False
    except Exception as e:
        logging.error(f"Error updating metainfo with pieces: {e}")
        return False