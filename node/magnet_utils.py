"""Utilities for handling magnet links and parsing info_hash"""
import logging
from urllib.parse import parse_qs, urlparse

def parse_magnet(magnet_url):
    """
    phân tích magnet link thành các thành phần cơ bản
    
    Parameters:
        magnet_url (str): Magnet URL
        
    Returns:
        dict: thông tin đã phân tích, bao gồm info_hash, name và trackers
    """
    if not magnet_url.startswith('magnet:?'):
        raise ValueError("Invalid magnet URL format")
    
    # Parse the query string
    query = magnet_url[8:]  # Remove 'magnet:?'
    params = parse_qs(query) 
    
    result = {}
    
    # trích xuất info_hash từ xt (exact topic)
    if 'xt' in params:
        for xt in params['xt']:
            if xt.startswith('urn:btih:'):
                result['info_hash'] = xt[9:].lower()
                break
    
    # Extract dn (display name)
    if 'dn' in params:
        result['name'] = params['dn'][0]
    
    # Extract tr (tracker URL)
    if 'tr' in params:
        result['trackers'] = params['tr']
    
    # Validate required fields
    if 'info_hash' not in result:
        raise ValueError("Missing info_hash in magnet URL")
    
    return result

def create_magnet(info_hash, name=None, trackers=None):
    """
    Create a magnet URL from info_hash and optional parameters
    
    Parameters:
        info_hash (str): Info hash of the torrent
        name (str, optional): Display name
        trackers (list, optional): List of tracker URLs
        
    Returns:
        str: Magnet URL
    """
    if not info_hash:
        raise ValueError("info_hash is required")
    
    # Start with the basic magnet link
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    
    # Add display name if provided
    if name:
        from urllib.parse import quote
        magnet += f"&dn={quote(name)}"
    
    # Add trackers if provided
    if trackers:
        for tracker in trackers:
            magnet += f"&tr={quote(tracker)}"
    
    return magnet