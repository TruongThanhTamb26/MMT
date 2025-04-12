#!/usr/bin/env python

# Script để tạo torrent và magnet link
import os
import sys

# Thêm đường dẫn hiện tại để có thể import các module
sys.path.append('.')

# Import các module cần thiết
from tracker.metainfo_manager import create_metainfo
from node.magnet_utils import create_magnet
from config import TRACKER_URL

def main():
    # Đường dẫn đến file cần tạo torrent
    file_path = 'test_file.txt'
    
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found!")
        return
    
    # Tạo metainfo từ file
    print(f"Creating metainfo for {file_path}...")
    info_hash, metainfo = create_metainfo(
        file_path, 
        piece_length=512*1024,  # 512 KB pieces
        tracker_url=TRACKER_URL
    )
    
    print(f"Created torrent with info_hash: {info_hash}")
    
    # Tạo magnet link
    magnet_link = create_magnet(
        info_hash=info_hash,
        name=metainfo['name'],
        trackers=[TRACKER_URL]
    )
    
    print(f"Magnet link: {magnet_link}")
    
    # Cập nhật file seeder_test.py và leecher_test.py
    seeder_path = os.path.join('node', 'seeder_test.py')
    leecher_path = os.path.join('node', 'leecher_test.py')
    
    # Đọc nội dung file
    for path in [seeder_path, leecher_path]:
        with open(path, 'r') as f:
            content = f.read()
            
        # Thay thế magnet link
        old_magnet = content.split("magnet = ")[1].split("\n")[0].strip("'\"")
        content = content.replace(old_magnet, magnet_link)
        
        # Ghi lại vào file
        with open(path, 'w') as f:
            f.write(content)
            
        print(f"Updated magnet link in {path}")
    
    print("\nSeeder và leecher đã được cập nhật với magnet link mới.")
    print("Hướng dẫn chạy:")
    print("1. Đảm bảo tracker server đang chạy")
    print("2. Chạy: python node/seeder_test.py")
    print("3. Trong terminal khác, chạy: python node/leecher_test.py")

if __name__ == "__main__":
    main()

"""
Tạo metainfo files và tính toán info_hash cho torrent
"""

import os
import sys
import json
import hashlib
from pathlib import Path

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

def create_metainfo(file_paths, piece_length=512*1024, tracker_url=None, name=None):
    """
    Tạo metainfo cho một hoặc nhiều file torrent
    
    Parameters:
        file_paths (list): Danh sách đường dẫn đến các file cần tạo metainfo
        piece_length (int): Kích thước mỗi phần (mặc định 512KB)
        tracker_url (str): URL của tracker
        name (str): Tên của torrent, nếu không cung cấp sẽ sử dụng tên thư mục chứa file
        
    Returns:
        tuple: (info_hash, metainfo) - info_hash là mã băm của thông tin torrent, metainfo là dữ liệu metainfo
    """
    if tracker_url is None:
        from config import TRACKER_URL
        tracker_url = TRACKER_URL
        
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
    
    # Tạo piece hashes nếu có
    piece_hashes = calculate_pieces_hash(file_paths, piece_length)
    metainfo["pieces"] = piece_hashes
    
    # Tính toán info_hash
    # Sử dụng SHA1 để tạo info_hash
    info_hash = hashlib.sha1(json.dumps(metainfo, sort_keys=True).encode()).hexdigest()
    metainfo["info_hash"] = info_hash
    
    return info_hash, metainfo

def save_metainfo(metainfo, output_dir="metainfo"):
    """
    Lưu metainfo vào file JSON
    
    Parameters:
        metainfo (dict): Dữ liệu metainfo đã tạo
        output_dir (str): Thư mục đầu ra
        
    Returns:
        str: Đường dẫn đến file metainfo đã tạo
    """
    # Tạo thư mục nếu chưa tồn tại
    os.makedirs(output_dir, exist_ok=True)
    
    # Đường dẫn file đầu ra
    output_path = Path(output_dir) / f"{metainfo['name']}.torrent.json"
    
    # Ghi metainfo vào file JSON
    with open(output_path, 'w') as f:
        json.dump(metainfo, f, indent=2)  # indent=2 để dễ đọc hơn
    
    return str(output_path)

def create_magnet_link(info_hash, name=None, tracker_url=None):
    """
    Tạo magnet link từ info_hash và các thông số tùy chọn
    
    Parameters:
        info_hash (str): Info hash của torrent
        name (str, optional): Display name
        tracker_url (str, optional): Tracker URL
        
    Returns:
        str: Magnet URL
    """
    from urllib.parse import quote
    
    # Tạo magnet URL cơ bản
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    
    # Thêm tên nếu được cung cấp
    if name:
        magnet += f"&dn={quote(name)}"
    
    # Thêm tracker URL nếu được cung cấp
    if tracker_url:
        magnet += f"&tr={quote(tracker_url)}"
    
    return magnet

def main():
    """
    Hàm chính để chạy từ command line
    
    Cách sử dụng:
    python create_torrent.py [-p PIECE_LENGTH] [-t TRACKER_URL] [-n NAME] file1 [file2 ...]
    """
    import argparse
    from config import TRACKER_URL
    
    parser = argparse.ArgumentParser(description='Create torrent metainfo file')
    parser.add_argument('files', nargs='+', help='File(s) to include in the torrent')
    parser.add_argument('-p', '--piece-length', type=int, default=512*1024, help='Piece length in bytes (default: 512KB)')
    parser.add_argument('-t', '--tracker', default=TRACKER_URL, help=f'Tracker URL (default: {TRACKER_URL})')
    parser.add_argument('-n', '--name', help='Torrent name (default: file name)')
    parser.add_argument('-o', '--output-dir', default='metainfo', help='Output directory (default: metainfo)')
    
    args = parser.parse_args()
    
    try:
        # Tạo metainfo
        info_hash, metainfo = create_metainfo(
            args.files, 
            piece_length=args.piece_length, 
            tracker_url=args.tracker,
            name=args.name
        )
        
        # Lưu metainfo vào file JSON
        output_path = save_metainfo(metainfo, args.output_dir)
        
        # Tạo magnet link
        magnet_link = create_magnet_link(info_hash, metainfo['name'], args.tracker)
        
        print(f"Torrent created successfully!")
        print(f"Info hash: {info_hash}")
        print(f"Metainfo file: {output_path}")
        print(f"Magnet link: {magnet_link}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()