""" Quản lý trạng thái các peer theo info_hash"""
import socket
import struct
import logging

# Dictionary lưu trữ thông tin peer theo info_hash
# Cấu trúc: {info_hash: [peer1, peer2, ...]}
peer_registry = {}

def manage_peer(peer):
    """
    Quản lý các peer dựa trên loại event và info_hash
    
    Parameters:
        peer (dict): Thông tin peer từ request
        
    Returns:
        dict: Phản hồi chứa danh sách peers và tracker_id
    """

    # Lấy thông tin cơ bản từ peer
    event = peer.get("event")
    info_hash = peer.get("info_hash")
    peer_id = peer.get("peer_id")
    compact = int(peer.get("compact", 0))

    # Khởi tạo danh sách trống nếu info_hash chưa tồn tại
    if info_hash not in peer_registry:
        peer_registry[info_hash] = []

    # Chuẩn bị thông tin peer để lưu trữ (chỉ những thông tin cần thiết)
    peer_info = {
        "peer_id": peer.get("peer_id"),
        "ip": peer.get("ip"),
        "port": int(peer.get("port")),
        "left": int(peer.get("left"))  # Để biết peer là seeder hay leecher
    }

    # Xử lý theo loại event
    if event == "started":
        # Kiểm tra nếu peer đã tồn tại, cập nhật thông tin
        existing_peer = next((p for p in peer_registry[info_hash] if p["peer_id"] == peer_id), None)
        if existing_peer:
            # Cập nhật thông tin peer hiện có
            existing_peer.update(peer_info)
        else:
            # Thêm peer mới vào danh sách
            peer_registry[info_hash].append(peer_info)
            
    elif event == "completed":
        # Đánh dấu peer là seeder (left = 0)
        existing_peer = next((p for p in peer_registry[info_hash] if p["peer_id"] == peer_id), None)
        if existing_peer:
            existing_peer["left"] = 0
        else:
            # Nếu không tìm thấy, thêm mới với left = 0
            peer_info["left"] = 0
            peer_registry[info_hash].append(peer_info)
            
    elif event == "stopped":
        # Xóa peer khỏi danh sách
        peer_registry[info_hash] = [p for p in peer_registry[info_hash] if p["peer_id"] != peer_id]
    
    # Chỉ trả về danh sách các peer khác, không bao gồm peer hiện tại
    other_peers = [p for p in peer_registry[info_hash] if p["peer_id"] != peer_id]
    
    response = {
        "tracker_id": "simple_tracker_v1",
        "failure_reason": None
    }
    
    # Hỗ trợ compact mode - nén danh sách peer thành dạng binary
    if compact:
        compact_peers = b""
        for p in other_peers:
            try:
                # Chuyển IP từ string sang 4 bytes
                ip_bytes = socket.inet_aton(p["ip"])
                # Chuyển port thành 2 bytes
                port_bytes = struct.pack("!H", p["port"])
                compact_peers += ip_bytes + port_bytes
            except Exception as e:
                logging.warning(f"Error compacting peer {p['ip']}:{p['port']}: {e}")
                continue
        response["peers"] = compact_peers
    else:
        # Dictionary model (mode không nén)
        response["peers"] = other_peers
    
    return response

def get_stats():
    """
    Trả về thống kê về số lượng torrent và peer
    
    Returns:
        dict: Thống kê về torrents, seeders, leechers
    """
    
    stats = {
        "torrents": len(peer_registry),
        "peers": 0,
        "seeders": 0,
        "leechers": 0
    }
    
    for info_hash, peers in peer_registry.items():
        stats["peers"] += len(peers)
        stats["seeders"] += sum(1 for p in peers if p["left"] == 0)
        stats["leechers"] += sum(1 for p in peers if p["left"] > 0)
        
    return stats