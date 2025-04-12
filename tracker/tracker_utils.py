""" Kiểm tra request của Peers"""

def validate_announce_payload(payload):
    required_fields = ["peer_id", "event", "info_hash", "ip", "port", "downloaded", "left", "uploaded", "compact"]
    missing_fields = [field for field in required_fields if field not in payload]

    if missing_fields:
        return f"Missing fields: {', '.join(missing_fields)}", None

    # Kiểm tra các trường bắt buộc
    try:
        int(payload["port"])
        int(payload["downloaded"])
        int(payload["left"])
        int(payload["uploaded"])
    except ValueError:
        return "Invalid type for port/downloaded/left/uploaded", None

    # Kiểm tra compact mode
    # compact=1 có nghĩa là client hỗ trợ danh sách nén theo bytes
    warning = None
    if "compact" in payload and payload["compact"] != 1:
        warning = "compact value should be 1, using compact mode by default"

    return None, warning