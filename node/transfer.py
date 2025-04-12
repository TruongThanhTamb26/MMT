"""Handle file transfers, piece management, and peer connections"""
import os
import time
import socket
import logging
import hashlib
import threading
from pathlib import Path
from collections import defaultdict, deque

class PieceManager:
    """Manages pieces of a torrent, tracks which are downloaded and needed"""
    
    def __init__(self, info_hash, piece_length, piece_count, files, repo_dir):
        """
        Initialize piece manager
        
        Parameters:
            info_hash (str): Info hash of the torrent
            piece_length (int): Length of each piece in bytes
            piece_count (int): Total number of pieces
            files (list): List of files in the torrent
            repo_dir (Path): Directory to save files
        """
        self.info_hash = info_hash
        self.piece_length = piece_length
        self.piece_count = piece_count
        self.files = files
        self.repo_dir = repo_dir
        
        # Create torrent directory
        self.torrent_dir = repo_dir / info_hash
        os.makedirs(self.torrent_dir, exist_ok=True)
        
        # Track piece status
        self.have_pieces = [False] * piece_count
        self.requested_pieces = set()
        
        # Track piece hashes for verification (loaded from metainfo)
        self.piece_hashes = []
        
        # Track statistics
        self.bytes_downloaded = 0
        self.bytes_uploaded = 0
        
        # Check for existing data
        self._check_existing_data()
        
        # For thread safety
        self.lock = threading.Lock()
    
    def set_piece_hashes(self, hashes):
        """Set the expected SHA-1 hashes for each piece"""
        self.piece_hashes = hashes
        
    def _check_existing_data(self):
        """Check for existing downloaded pieces"""
        # In a real implementation, you would check piece hashes
        # and mark pieces as downloaded if they match
        
        # Simplified: Just check if files exist and estimate progress
        total_expected_size = sum(f['length'] for f in self.files)
        total_existing_size = 0
        
        for file_info in self.files:
            file_path = self.torrent_dir / file_info['path']
            if file_path.exists():
                total_existing_size += file_path.stat().st_size
                
            # Kiểm tra các piece đã tải
            for i in range(self.piece_count):
                piece_file = self.torrent_dir / f"piece_{i}.tmp"
                if piece_file.exists():
                    # Kiểm tra nếu có hashes để verify
                    if self.piece_hashes and i < len(self.piece_hashes):
                        with open(piece_file, 'rb') as f:
                            data = f.read()
                            if self.verify_piece(i, data):
                                self.have_pieces[i] = True
                    else:
                        # Nếu không có hash để xác minh, đánh dấu là đã có
                        self.have_pieces[i] = True
        
        # Estimate downloaded bytes
        if total_expected_size > 0:
            self.bytes_downloaded = min(total_existing_size, total_expected_size)
    
    @property
    def bytes_left(self):
        """Calculate bytes left to download"""
        total_size = sum(f['length'] for f in self.files)
        return max(0, total_size - self.bytes_downloaded)
    
    @property
    def progress(self):
        """Calculate download progress (0.0 to 1.0)"""
        if self.piece_count == 0:
            return 1.0
        return sum(1 for p in self.have_pieces if p) / self.piece_count
    
    def get_next_request(self, peer_has_pieces):
        """
        Get next piece to request from a peer
        
        Parameters:
            peer_has_pieces (list): Boolean list of pieces the peer has
            
        Returns:
            int or None: Next piece index to request or None if none available
        """
        with self.lock:
            # Find pieces that we need and the peer has
            candidates = []
            for i in range(self.piece_count):
                if not self.have_pieces[i] and i not in self.requested_pieces and peer_has_pieces[i]:
                    candidates.append(i)
            
            if not candidates:
                return None
            
            # Use rarest-first strategy instead of just picking the first piece
            rarest_piece = self.get_rarest_piece(candidates)
            if rarest_piece is not None:
                self.requested_pieces.add(rarest_piece)
                return rarest_piece
                
            # Fallback to first piece if rarest strategy fails
            piece_index = candidates[0]
            self.requested_pieces.add(piece_index)
            return piece_index
    
    def get_rarest_piece(self, candidates):
        """
        Finds the rarest piece among candidates using the Rarest First strategy
        
        Parameters:
            candidates (list): List of candidate piece indexes
            
        Returns:
            int or None: Index of the rarest piece or None if no candidates
        """
        if not candidates:
            return None
            
        # In a full implementation, we would track how many peers have each piece
        # For this implementation, we'll use a simple randomization to distribute requests
        # which is better than sequential for bandwidth utilization in MDDT
        import random
        return random.choice(candidates)
    
    def verify_piece(self, piece_index, data):
        """
        Verify the integrity of a piece using SHA-1 hash
        
        Parameters:
            piece_index (int): Index of the piece
            data (bytes): Piece data
            
        Returns:
            bool: True if piece is valid, False otherwise
        """
        # If we don't have hashes to verify against, assume it's valid
        if not self.piece_hashes or piece_index >= len(self.piece_hashes):
            logging.warning(f"No hash available for piece {piece_index}, skipping verification")
            return True
            
        # Calculate SHA-1 hash of the data
        sha1 = hashlib.sha1(data).hexdigest()
        
        # Compare with expected hash
        expected_hash = self.piece_hashes[piece_index]
        is_valid = sha1 == expected_hash
        
        if not is_valid:
            logging.warning(f"Piece {piece_index} failed verification: got {sha1}, expected {expected_hash}")
        
        return is_valid
    
    def receive_piece(self, piece_index, data):
        """
        Process a received piece, verify it, and save it
        
        Parameters:
            piece_index (int): Index of the piece
            data (bytes): Piece data
            
        Returns:
            bool: True if piece was valid and saved, False otherwise
        """
        with self.lock:
            # Validate piece size
            if len(data) > self.piece_length:
                logging.error(f"Received piece {piece_index} is too large")
                return False
                
            # Verify piece hash
            if not self.verify_piece(piece_index, data):
                # If verification fails, remove from requested pieces so we can try again
                self.requested_pieces.discard(piece_index)
                return False
            
            # In multi-file torrents, pieces can span multiple files
            # For simplicity, we'll save each piece to a temporary file first
            piece_file = self.torrent_dir / f"piece_{piece_index}.tmp"
            
            try:
                with open(piece_file, 'wb') as f:
                    f.write(data)
                
                # Mark piece as downloaded
                self.have_pieces[piece_index] = True
                self.requested_pieces.discard(piece_index)
                self.bytes_downloaded += len(data)
                
                # When all pieces are downloaded, assemble the files
                if all(self.have_pieces):
                    self._assemble_files()
                
                return True
                
            except Exception as e:
                logging.error(f"Error saving piece {piece_index}: {e}")
                return False
    
    def _assemble_files(self):
        """
        Assemble final files from pieces after all pieces are downloaded
        """
        logging.info("Tất cả các mảnh đã tải xong, đang ghép file...")
        
        # Lặp qua từng file trong danh sách files
        offset = 0  # Vị trí bắt đầu của file hiện tại trong toàn bộ dữ liệu
        
        for file_info in self.files:
            file_path = self.torrent_dir / file_info['path']
            file_length = file_info['length']
            
            # Tạo thư mục chứa file nếu cần
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Mở file đích để ghi dữ liệu
            with open(file_path, 'wb') as output_file:
                # Tính toán mảnh đầu tiên và cuối cùng chứa dữ liệu của file này
                first_piece = offset // self.piece_length
                last_piece = (offset + file_length - 1) // self.piece_length if file_length > 0 else first_piece
                
                # Vị trí bắt đầu trong mảnh đầu tiên
                first_piece_offset = offset % self.piece_length
                
                # Lặp qua các mảnh cần thiết
                for piece_index in range(first_piece, last_piece + 1):
                    piece_file = self.torrent_dir / f"piece_{piece_index}.tmp"
                    
                    if piece_file.exists():
                        with open(piece_file, 'rb') as piece_data:
                            # Đối với mảnh đầu tiên, có thể cần bỏ qua một số byte đầu tiên
                            if piece_index == first_piece and first_piece_offset > 0:
                                piece_data.seek(first_piece_offset)
                            
                            # Đọc dữ liệu từ mảnh và ghi vào file đích
                            data = piece_data.read()
                            
                            # Nếu là mảnh cuối cùng, chỉ ghi đến hết file
                            if piece_index == last_piece:
                                bytes_needed = (offset + file_length) - (piece_index * self.piece_length)
                                if bytes_needed < len(data):
                                    data = data[:bytes_needed]
                            
                            output_file.write(data)
                    else:
                        logging.error(f"Không tìm thấy mảnh {piece_index}")
            
            # Cập nhật offset cho file tiếp theo
            offset += file_length
            
            logging.info(f"Đã tạo file: {file_path}")
        
        # Xóa các file tạm
        for i in range(self.piece_count):
            piece_file = self.torrent_dir / f"piece_{i}.tmp"
            if piece_file.exists():
                try:
                    os.unlink(piece_file)
                except Exception as e:
                    logging.error(f"Lỗi khi xóa file tạm: {e}")
                    
        logging.info("Hoàn tất ghép file!")


class ConnectionManager:
    """Manages connections to peers for a torrent"""
    
    def __init__(self, info_hash, piece_manager, peer_id, announce_callback):
        """
        Initialize connection manager
        
        Parameters:
            info_hash (str): Info hash of the torrent
            piece_manager (PieceManager): Piece manager for this torrent
            peer_id (str): Our peer ID
            announce_callback (callable): Function to call for tracker announces
        """
        self.info_hash = info_hash
        self.piece_manager = piece_manager
        self.peer_id = peer_id
        self.announce_callback = announce_callback
        
        # Active peer connections
        self.peers = {}
        
        # For thread safety
        self.lock = threading.Lock()
        
        # Control flags
        self.running = False
        
    def add_peer(self, peer_info):
        """
        Add a peer to connect to
        
        Parameters:
            peer_info (dict): Peer information from tracker
        """
        with self.lock:
            peer_id = peer_info.get('peer_id')
            if not peer_id:
                logging.warning(f"Peer info missing peer_id: {peer_info}")
                return
                
            # Avoid connecting to ourselves
            if peer_id == self.peer_id:
                return
                
            # Avoid reconnecting to existing peers
            if peer_id in self.peers:
                return
            
            # Create a peer connection handler
            peer_handler = PeerConnection(
                peer_info=peer_info,
                info_hash=self.info_hash,
                piece_manager=self.piece_manager,
                our_peer_id=self.peer_id
            )
            
            self.peers[peer_id] = {
                'info': peer_info,
                'handler': peer_handler,
                'connected': False,
                'last_seen': time.time()
            }
    
    def start(self):
        """Start connection manager and connect to peers"""
        if self.running:
            return
            
        self.running = True
        
        # Start connection thread
        threading.Thread(target=self._connection_loop, daemon=True).start()
        
        # Start server thread to accept incoming connections
        threading.Thread(target=self._start_server, daemon=True).start()
        
        # Start announce thread
        threading.Thread(target=self._announce_loop, daemon=True).start()
        
        # Start end game thread
        threading.Thread(target=self._end_game_loop, daemon=True).start()
        
        # Thêm thread theo dõi hiệu suất MDDT
        threading.Thread(target=self._mddt_stats_loop, daemon=True).start()
    
    def stop(self):
        """Stop all connections and shut down"""
        if not self.running:
            return
            
        self.running = False
        
        with self.lock:
            # Close all peer connections
            for peer_id, peer in list(self.peers.items()):
                try:
                    if peer['handler']:
                        peer['handler'].close()
                except Exception as e:
                    logging.error(f"Error closing connection to peer {peer_id}: {e}")
            
            # Clear peer list
            self.peers.clear()
            
            # Final announce to tracker with stopped event
            try:
                self.announce_callback()
            except Exception as e:
                logging.error(f"Error sending final announce: {e}")
                
            logging.info("Connection manager stopped successfully")
    
    def _connection_loop(self):
        """Main loop for managing peer connections"""
        retry_counter = {}  # Đếm số lần thử lại kết nối
        
        while self.running:
            with self.lock:
                # Try to connect to peers that aren't connected
                for peer_id, peer in self.peers.items():
                    if not peer['connected'] and peer['handler']:
                        # Kiểm tra số lần thử kết nối
                        if peer_id not in retry_counter:
                            retry_counter[peer_id] = 0
                        
                        # Nếu đã thử quá nhiều lần, tạm thời bỏ qua peer này
                        if retry_counter[peer_id] >= 3:
                            if time.time() - peer.get('last_retry', 0) < 60:  # Đợi 1 phút trước khi thử lại
                                continue
                            else:
                                # Reset bộ đếm sau thời gian chờ
                                retry_counter[peer_id] = 0
                        
                        try:
                            logging.info(f"Thử kết nối đến peer {peer_id} (IP: {peer['info'].get('ip')}:{peer['info'].get('port')})")
                            peer['handler'].connect()
                            peer['connected'] = True
                            retry_counter[peer_id] = 0  # Reset counter on success
                            logging.info(f"Đã kết nối thành công đến peer {peer_id}")
                        except ConnectionError as e:
                            retry_counter[peer_id] += 1
                            peer['last_retry'] = time.time()
                            logging.error(f"Lỗi kết nối đến peer {peer_id}: {e}")
                        except Exception as e:
                            retry_counter[peer_id] += 1
                            peer['last_retry'] = time.time()
                            logging.error(f"Lỗi không xác định khi kết nối đến peer {peer_id}: {e}")
                
                # Check for dead connections
                now = time.time()
                for peer_id, peer in list(self.peers.items()):
                    # Nếu kết nối đã được thiết lập nhưng đã lâu không có hoạt động
                    if peer['connected'] and peer['handler']:
                        if now - peer['handler'].last_activity > 120:  # 2 phút timeout
                            logging.info(f"Peer {peer_id} không hoạt động, đóng kết nối")
                            peer['handler'].close()
                            peer['connected'] = False
                            
                            # Đặt lại bộ đếm, để có thể thử kết nối lại
                            retry_counter[peer_id] = 0
                    
                    # Cập nhật thời gian last_seen
                    if peer['connected'] and peer['handler']:
                        peer['last_seen'] = peer['handler'].last_activity
            
            # Sleep before next check
            time.sleep(5)
    
    def _start_server(self):
        """Start server socket to accept incoming connections"""
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('0.0.0.0', 6881))
            server_socket.listen(5)
            
            logging.info("Listening for incoming connections on port 6881")
            
            while self.running:
                try:
                    # Accept new connections
                    client_socket, address = server_socket.accept()
                    logging.info(f"Incoming connection from {address}")
                    
                    # Handle the connection in a new thread
                    threading.Thread(
                        target=self._handle_incoming_connection,
                        args=(client_socket, address),
                        daemon=True
                    ).start()
                
                except Exception as e:
                    if self.running:
                        logging.error(f"Error accepting connection: {e}")
        
        except Exception as e:
            logging.error(f"Error starting server: {e}")
        
        finally:
            try:
                server_socket.close()
            except:
                pass
    
    def _handle_incoming_connection(self, client_socket, address):
        """
        Handle incoming peer connection
        
        Parameters:
            client_socket (socket): Connected socket
            address (tuple): Address of the peer
        """
        # In a real implementation, handle the protocol handshake
        # and add the peer to our list if it's requesting our info_hash
        
        # For this simplified version, just note that it would happen here
        logging.info(f"Would handle incoming connection from {address}")
        
        # Close the socket
        try:
            client_socket.close()
        except:
            pass
    
    def _announce_loop(self):
        """Periodically announce to tracker to get updated peer list"""
        while self.running:
            try:
                peers = self.announce_callback()
                
                # Add new peers
                for peer in peers:
                    self.add_peer(peer)
                    
                logging.info(f"Announced to tracker, got {len(peers)} peers")
            
            except Exception as e:
                logging.error(f"Error in announce: {e}")
            
            # Announce every 30 seconds
            time.sleep(30)
    
    def _end_game_loop(self):
        """
        End game strategy: khi tải xuống gần hoàn tất, gửi request cho tất cả 
        các peer có các mảnh còn thiếu để hoàn tất tải xuống nhanh chóng
        """
        while self.running:
            try:
                # Chỉ kích hoạt end game khi tải xuống gần hoàn tất (>95%)
                if self.piece_manager.progress > 0.95 and self.piece_manager.progress < 1.0:
                    # Tìm các mảnh còn thiếu
                    missing_pieces = []
                    for i in range(len(self.piece_manager.have_pieces)):
                        if not self.piece_manager.have_pieces[i] and i not in self.piece_manager.requested_pieces:
                            missing_pieces.append(i)
                    
                    if missing_pieces:
                        logging.info(f"End game strategy activated. Missing pieces: {len(missing_pieces)}")
                        
                        # Yêu cầu tất cả peer có mảnh còn thiếu
                        with self.lock:
                            for peer_id, peer in self.peers.items():
                                if peer['connected'] and peer['handler']:
                                    for piece_index in missing_pieces:
                                        if peer['handler'].peer_bitfield[piece_index]:
                                            # Thêm yêu cầu vào hàng đợi của peer này
                                            peer['handler'].request_piece(piece_index)
                
            except Exception as e:
                logging.error(f"Error in end game loop: {e}")
            
            # Kiểm tra mỗi 5 giây
            time.sleep(5)
    
    def _mddt_stats_loop(self):
        """
        Theo dõi và ghi log hiệu suất của MDDT (tải xuống nhiều mảnh từ nhiều nguồn)
        """
        while self.running:
            try:
                if len(self.peers) > 1:  # Chỉ có ý nghĩa khi có nhiều hơn 1 peer
                    active_peers = 0
                    total_download_speed = 0
                    peer_speeds = {}
                    
                    with self.lock:
                        for peer_id, peer in self.peers.items():
                            if peer['connected'] and peer['handler']:
                                active_peers += 1
                                # Lưu trữ bytes đã tải xuống từ peer này
                                peer_speeds[peer_id] = peer['handler'].bytes_downloaded
                    
                    if active_peers > 1:
                        # Tính toán tốc độ tải xuống
                        time.sleep(1)  # Đợi 1 giây để có số liệu mới
                        
                        with self.lock:
                            for peer_id, peer in self.peers.items():
                                if peer_id in peer_speeds and peer['connected'] and peer['handler']:
                                    # Tính bytes tải xuống trong 1 giây
                                    new_bytes = peer['handler'].bytes_downloaded - peer_speeds[peer_id]
                                    total_download_speed += new_bytes
                        
                        # Log hiệu suất MDDT
                        if total_download_speed > 0:
                            logging.info(
                                f"MDDT Performance: {active_peers} peers active, "
                                f"Total speed: {total_download_speed/1024:.2f} KB/s, "
                                f"Progress: {self.piece_manager.progress*100:.2f}%"
                            )
                
            except Exception as e:
                logging.error(f"Error in MDDT stats loop: {e}")
            
            # Cập nhật mỗi 10 giây
            time.sleep(10)


class PeerConnection:
    """Handles communication with a single peer"""
    
    def __init__(self, peer_info, info_hash, piece_manager, our_peer_id):
        """
        Initialize peer connection
        
        Parameters:
            peer_info (dict): Peer information from tracker
            info_hash (str): Info hash of the torrent
            piece_manager (PieceManager): Piece manager for this torrent
            our_peer_id (str): Our peer ID
        """
        self.peer_info = peer_info
        self.info_hash = info_hash
        self.piece_manager = piece_manager
        self.our_peer_id = our_peer_id
        
        self.ip = peer_info.get('ip')
        self.port = peer_info.get('port')
        self.peer_id = peer_info.get('peer_id')
        
        self.socket = None
        self.connected = False
        self.peer_choking = True  # Mặc định ban đầu là bị chặn
        self.am_choking = True    # Mặc định ban đầu là ta chặn peer
        self.peer_interested = False
        self.am_interested = False
        
        # For tracking peer's available pieces
        self.peer_bitfield = [False] * piece_manager.piece_count
        
        # Request queue for sending piece requests to peer
        self.request_queue = deque()
        
        # Statistics for this connection
        self.bytes_downloaded = 0
        self.bytes_uploaded = 0
        self.last_activity = time.time()
        
        # Maximum number of outstanding requests
        self.max_requests = 10  # Tăng số lượng request đồng thời để cải thiện MDDT
        
        # For thread safety
        self.lock = threading.Lock()

    def connect(self):
        """Connect to the peer and perform handshake"""
        if self.connected:
            return True
        
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)  # 10 second timeout
            logging.info(f"Connecting to peer {self.peer_id} at {self.ip}:{self.port}")
            self.socket.connect((self.ip, self.port))
            
            # Perform the BitTorrent protocol handshake
            resp_peer_id = self._perform_handshake()
            if resp_peer_id != self.peer_id:
                logging.warning(f"Peer responded with different peer_id: {resp_peer_id} vs {self.peer_id}")
                
            self.connected = True
            self.last_activity = time.time()
            
            # Send interested message to peer
            self._send_interested()
            
            # Start communication threads
            threading.Thread(target=self._receiver_loop, daemon=True).start()
            threading.Thread(target=self._request_loop, daemon=True).start()
            
            return True
            
        except Exception as e:
            logging.error(f"Error connecting to peer {self.peer_id} ({self.ip}:{self.port}): {e}")
            self.close()
            return False
    
    def _perform_handshake(self):
        """Perform the BitTorrent protocol handshake with the peer"""
        # Handshake: <pstrlen><pstr><reserved><info_hash><peer_id>
        pstr = "BitTorrent protocol"
        reserved = b'\x00' * 8
        
        # Chuyển đổi info_hash từ hex string sang bytes nếu cần
        info_hash_bytes = bytes.fromhex(self.info_hash) if isinstance(self.info_hash, str) else self.info_hash
        
        # Chuyển đổi peer_id sang bytes nếu cần
        peer_id_bytes = self.our_peer_id.encode() if isinstance(self.our_peer_id, str) else self.our_peer_id
        
        # Construct handshake message
        handshake = bytes([len(pstr)]) + \
                    pstr.encode() + \
                    reserved + \
                    info_hash_bytes + \
                    peer_id_bytes
                    
        logging.debug(f"Sending handshake to {self.ip}:{self.port}")
        # Send handshake
        self.socket.sendall(handshake)
        
        try:
            # Receive and parse peer's handshake
            response = self._read_exactly(len(handshake))
            if not response:
                raise ConnectionError("Peer did not respond with handshake")
            
            logging.debug(f"Received handshake from {self.ip}:{self.port}")
                
            # Validate response contains correct info_hash
            resp_info_hash = response[28:48].hex()
            if resp_info_hash != (self.info_hash if isinstance(self.info_hash, str) else self.info_hash.hex()):
                raise ValueError(f"Peer responded with wrong info_hash: {resp_info_hash}")
                
            # Extract peer_id from response
            resp_peer_id = response[48:68]
            # Chuyển peer_id sang string để dễ so sánh và sử dụng
            resp_peer_id_str = resp_peer_id.decode(errors='replace')
            return resp_peer_id_str
        except socket.timeout:
            raise ConnectionError("Handshake timed out")
        except UnicodeDecodeError:
            # Xử lý trường hợp peer_id không thể decode
            logging.warning(f"Could not decode peer_id, using hex representation instead")
            return response[48:68].hex()
    
    def close(self):
        """Close the connection to the peer"""
        with self.lock:
            self.connected = False
            
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None

    def _receiver_loop(self):
        """Handle incoming messages from peer"""
        logging.info(f"Starting receiver loop for peer {self.peer_id}")
        while self.connected:
            try:
                # Read message length (4 bytes big-endian)
                len_bytes = self._read_exactly(4)
                if not len_bytes:
                    logging.warning("Connection closed by peer")
                    break
                    
                msg_length = int.from_bytes(len_bytes, byteorder='big')
                
                # Keep-alive message
                if msg_length == 0:
                    logging.debug("Received keep-alive message")
                    self.last_activity = time.time()
                    continue
                    
                # Read message ID and payload
                msg_id_bytes = self._read_exactly(1)
                if not msg_id_bytes:
                    break
                    
                msg_id = msg_id_bytes[0]
                payload = self._read_exactly(msg_length - 1) if msg_length > 1 else b''
                
                # Update last activity time
                self.last_activity = time.time()
                
                # Process different message types
                if msg_id == 0:  # choke
                    logging.debug(f"Peer {self.peer_id} choked us")
                    self.peer_choking = True
                elif msg_id == 1:  # unchoke
                    logging.debug(f"Peer {self.peer_id} unchoked us")
                    self.peer_choking = False
                elif msg_id == 2:  # interested
                    logging.debug(f"Peer {self.peer_id} is interested")
                    self.peer_interested = True
                    # Since peer is interested, we should unchoke them to allow uploads
                    if self.am_choking:
                        self._send_unchoke()
                elif msg_id == 3:  # not interested
                    logging.debug(f"Peer {self.peer_id} is not interested")
                    self.peer_interested = False
                elif msg_id == 4:  # have
                    piece_index = int.from_bytes(payload, byteorder='big')
                    logging.debug(f"Peer {self.peer_id} has piece {piece_index}")
                    self.peer_bitfield[piece_index] = True
                    # If we need this piece, express interest
                    if not self.piece_manager.have_pieces[piece_index]:
                        if not self.am_interested:
                            self._send_interested()
                elif msg_id == 5:  # bitfield
                    logging.debug(f"Received bitfield from peer {self.peer_id}")
                    self._process_bitfield(payload)
                    # Check if peer has pieces we need
                    for i, has_piece in enumerate(self.peer_bitfield):
                        if has_piece and not self.piece_manager.have_pieces[i]:
                            if not self.am_interested:
                                self._send_interested()
                            break
                elif msg_id == 6:  # request
                    logging.debug(f"Received request from peer {self.peer_id}")
                    self._process_request(payload)
                elif msg_id == 7:  # piece
                    # Extract piece info: <index><begin><block>
                    if len(payload) < 8:
                        continue
                    
                    index = int.from_bytes(payload[0:4], byteorder='big')
                    begin = int.from_bytes(payload[4:8], byteorder='big')
                    block = payload[8:]
                    
                    logging.debug(f"Received piece {index} ({len(block)} bytes) from peer {self.peer_id}")
                    
                    # Update download statistics
                    self.bytes_downloaded += len(block)
                    
                    # Process the piece data
                    result = self.piece_manager.receive_piece(index, block)
                    if result:
                        logging.info(f"Successfully received piece {index} from peer {self.peer_id}")
                        
                        # Send have message to all peers
                        # (In a full implementation, this would be done by the ConnectionManager)
                        
                        # Since we've got a piece, update our requests
                        with self.lock:
                            # Remove any requests for this piece from the queue
                            self.request_queue = deque([r for r in self.request_queue if r[0] != index])
                    else:
                        logging.warning(f"Failed to process piece {index} from peer {self.peer_id}")
                
                # Gửi keep-alive message mỗi 2 phút nếu không có hoạt động nào
                if time.time() - self.last_activity > 120:
                    self._send_keep_alive()
                
            except Exception as e:
                logging.error(f"Error in receiver loop with peer {self.peer_id}: {e}")
                break
                
        self.close()

    def _read_exactly(self, n):
        """Read exactly n bytes from socket"""
        if n <= 0:
            return b''
            
        data = b''
        while len(data) < n:
            try:
                self.socket.settimeout(10)  # Đặt timeout là 10 giây
                packet = self.socket.recv(n - len(data))
                
                if not packet:  # Kết nối đã đóng
                    logging.warning(f"Connection to {self.peer_id} closed when reading data")
                    return None
                    
                data += packet
                
            except socket.timeout:
                # Xử lý timeout - có thể thử lại một lần nữa
                logging.warning(f"Socket timeout when reading from {self.peer_id}, retrying...")
                try:
                    # Thử lại thêm một lần với timeout dài hơn
                    self.socket.settimeout(15)
                    packet = self.socket.recv(n - len(data))
                    if packet:
                        data += packet
                    else:
                        return None
                except Exception:
                    # Nếu vẫn thất bại, đầu hàng
                    return None
                    
            except ConnectionResetError:
                logging.error(f"Connection reset by peer {self.peer_id}")
                return None
                
            except Exception as e:
                logging.error(f"Error reading from socket ({self.peer_id}): {e}")
                return None
                
        return data

    def _request_loop(self):
        """Request pieces from peer"""
        logging.info(f"Starting request loop for peer {self.peer_id}")
        
        while self.connected:
            try:
                # Don't request if peer is choking us
                if self.peer_choking:
                    time.sleep(1)
                    continue

                # If our request queue is empty, fill it with new pieces to request
                with self.lock:
                    if len(self.request_queue) < self.max_requests:
                        self._fill_request_queue()
                    
                    # If still empty after filling, sleep and try again later
                    if not self.request_queue:
                        time.sleep(1)
                        continue
                
                    # Send requests for multiple blocks at once (MDDT optimization)
                    # Gửi nhiều request cùng một lúc để tối ưu MDDT
                    requests_to_send = min(self.max_requests - len(self.request_queue) + 1, 
                                          len(self.request_queue))
                    
                    for _ in range(requests_to_send):
                        if self.request_queue:
                            piece_index, begin, length = self.request_queue.popleft()
                            self._send_request(piece_index, begin, length)
                
                # Wait a bit before next request batch to avoid flooding
                time.sleep(0.1)  # Shorter delay to increase throughput
                
            except Exception as e:
                logging.error(f"Error in request loop with peer {self.peer_id}: {e}")
                break
        
        self.close()
    
    def _fill_request_queue(self):
        """Fill the request queue with blocks to download"""
        # Maximum number of outstanding requests
        max_queue_size = self.max_requests * 2
        
        # If queue already has enough requests, don't add more
        if len(self.request_queue) >= max_queue_size:
            return
            
        # Get next piece to request using rarest first strategy
        piece_index = self.piece_manager.get_next_request(self.peer_bitfield)
        if piece_index is None:
            return
            
        # For the selected piece, create multiple block requests
        piece_size = self.piece_manager.piece_length
        block_size = min(16384, piece_size)  # 16KB standard block size
        
        # Add block requests for this piece to the queue
        num_blocks = (piece_size + block_size - 1) // block_size
        for i in range(num_blocks):
            begin = i * block_size
            length = min(block_size, piece_size - begin)
            self.request_queue.append((piece_index, begin, length))
            
        logging.debug(f"Added {num_blocks} blocks for piece {piece_index} to request queue")

    def _process_bitfield(self, payload):
        """Process a bitfield message from peer"""
        piece_count = len(self.peer_bitfield)
        expected_length = (piece_count + 7) // 8  # Ceiling division
        
        if len(payload) < expected_length:
            logging.warning(f"Received invalid bitfield length: got {len(payload)}, expected {expected_length}")
            return
        
        # Each bit in the bitfield represents a piece
        for i in range(piece_count):
            byte_index = i // 8
            if byte_index >= len(payload):
                break
                
            bit_index = 7 - (i % 8)  # Bits are sent in big-endian order
            has_piece = (payload[byte_index] >> bit_index) & 1
            
            if has_piece:
                self.peer_bitfield[i] = True
        
        logging.debug(f"Peer {self.peer_id} has {sum(self.peer_bitfield)} pieces")
    
    def _process_request(self, payload):
        """Process a request message from a peer"""
        # Only respond if we're not choking the peer
        if self.am_choking:
            logging.debug(f"Ignoring request from {self.peer_id} because we're choking them")
            return
            
        # Extract request parameters
        if len(payload) < 12:
            logging.warning("Invalid request message size")
            return
            
        piece_index = int.from_bytes(payload[0:4], byteorder='big')
        begin = int.from_bytes(payload[4:8], byteorder='big')
        length = int.from_bytes(payload[8:12], byteorder='big')
        
        # Check if we have the requested piece
        if piece_index >= len(self.piece_manager.have_pieces) or not self.piece_manager.have_pieces[piece_index]:
            logging.warning(f"Peer {self.peer_id} requested piece {piece_index} which we don't have")
            return
            
        # Read the piece data from our storage
        try:
            # In a real implementation, read from the actual file
            # For simplicity, we'll just read from the piece file
            piece_file = self.piece_manager.torrent_dir / f"piece_{piece_index}.tmp"
            
            if not piece_file.exists():
                logging.warning(f"Piece file not found: {piece_file}")
                return
                
            with open(piece_file, 'rb') as f:
                f.seek(begin)
                data = f.read(length)
                
            if len(data) != length:
                logging.warning(f"Could not read full block: got {len(data)}, expected {length}")
                return
                
            # Send the piece data to the peer
            self._send_piece(piece_index, begin, data)
            self.bytes_uploaded += len(data)
            self.piece_manager.bytes_uploaded += len(data)
            
        except Exception as e:
            logging.error(f"Error processing request for piece {piece_index}: {e}")

    def _send_message(self, msg_id, payload=b''):
        """Send a BitTorrent protocol message"""
        with self.lock:
            if not self.connected or not self.socket:
                return False
                
            # Message format: <length prefix><message ID><payload>
            msg_length = len(payload) + 1  # +1 for message ID
            msg = msg_length.to_bytes(4, byteorder='big') + bytes([msg_id]) + payload
            
            try:
                self.socket.sendall(msg)
                self.last_activity = time.time()
                return True
            except Exception as e:
                logging.error(f"Error sending message to {self.peer_id}: {e}")
                self.close()
                return False

    def _send_keep_alive(self):
        """Send a keep-alive message (zero-length message)"""
        if not self.connected or not self.socket:
            return False
            
        try:
            self.socket.sendall((0).to_bytes(4, byteorder='big'))
            self.last_activity = time.time()
            return True
        except Exception as e:
            logging.error(f"Error sending keep-alive to {self.peer_id}: {e}")
            self.close()
            return False

    def _send_interested(self):
        """Send an interested message to the peer"""
        logging.debug(f"Sending interested message to {self.peer_id}")
        result = self._send_message(2)  # 2 = interested
        if result:
            self.am_interested = True
        return result
        
    def _send_not_interested(self):
        """Send a not-interested message to the peer"""
        logging.debug(f"Sending not-interested message to {self.peer_id}")
        result = self._send_message(3)  # 3 = not interested
        if result:
            self.am_interested = False
        return result
    
    def _send_choke(self):
        """Send a choke message to the peer"""
        logging.debug(f"Sending choke message to {self.peer_id}")
        result = self._send_message(0)  # 0 = choke
        if result:
            self.am_choking = True
        return result
    
    def _send_unchoke(self):
        """Send an unchoke message to the peer"""
        logging.debug(f"Sending unchoke message to {self.peer_id}")
        result = self._send_message(1)  # 1 = unchoke
        if result:
            self.am_choking = False
        return result
        
    def _send_have(self, piece_index):
        """Send a have message to the peer"""
        logging.debug(f"Sending have message for piece {piece_index} to {self.peer_id}")
        payload = piece_index.to_bytes(4, byteorder='big')
        return self._send_message(4, payload)  # 4 = have
        
    def _send_request(self, piece_index, begin, length):
        """Send a request for a piece block"""
        logging.debug(f"Requesting piece {piece_index}, offset {begin}, length {length} from {self.peer_id}")
        payload = piece_index.to_bytes(4, byteorder='big') + \
                begin.to_bytes(4, byteorder='big') + \
                length.to_bytes(4, byteorder='big')
        return self._send_message(6, payload)  # 6 = request
        
    def _send_piece(self, piece_index, begin, data):
        """Send a piece block to the peer"""
        logging.debug(f"Sending piece {piece_index}, offset {begin}, length {len(data)} to {self.peer_id}")
        payload = piece_index.to_bytes(4, byteorder='big') + \
                begin.to_bytes(4, byteorder='big') + \
                data
        return self._send_message(7, payload)  # 7 = piece

    def request_piece(self, piece_index):
        """
        Gửi request cho một piece cụ thể (dùng trong End Game strategy)
        
        Parameters:
            piece_index (int): Chỉ số của piece cần tải xuống
        
        Returns:
            bool: True nếu request được gửi thành công
        """
        # Kiểm tra xem peer có piece này không
        if not self.peer_bitfield[piece_index]:
            return False
            
        # Kiểm tra xem chúng ta đã có piece này chưa
        if self.piece_manager.have_pieces[piece_index]:
            return False
            
        # Nếu peer đang choke chúng ta, không thể gửi request
        if self.peer_choking:
            return False
            
        # Tạo các request cho từng block của piece
        piece_size = self.piece_manager.piece_length
        block_size = min(16384, piece_size)  # 16KB standard block size
        
        # Thêm các request vào hàng đợi
        with self.lock:
            num_blocks = (piece_size + block_size - 1) // block_size
            for i in range(num_blocks):
                begin = i * block_size
                length = min(block_size, piece_size - begin)
                
                # Thêm request vào đầu hàng đợi (ưu tiên cao nhất)
                self.request_queue.appendleft((piece_index, begin, length))
                
            logging.debug(f"Đã thêm {num_blocks} blocks cho piece {piece_index} vào hàng đợi (End Game)")
            
        return True

