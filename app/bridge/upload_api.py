"""图片上传桥接层。"""

import base64
import threading

from .base import BridgeBase


class UploadApi(BridgeBase):
    def _init_upload(self):
        from ..core.image_upload import ImageUploader
        self._uploader = ImageUploader()

    def upload_image(self, data_url, provider="imgur"):
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:
            return {"ok": False, "err": "解码图片失败: %s" % e}

        if provider == "custom":
            url = self.cfg.get("upload_custom_url", "")
            key = self.cfg.get("upload_custom_key", "")
            if not url:
                return {"ok": False, "err": "请先配置自定义上传地址"}
            result = self._uploader.upload_custom(raw, url, key)
        else:
            result = self._uploader.upload_imgur(raw)
        return result

    def upload_history(self, limit=50, offset=0):
        entries = self._uploader.list_history(int(limit), int(offset))
        return {"ok": True, "data": entries}

    def upload_delete_history(self, entry_id):
        self._uploader.delete_history(int(entry_id))
        return {"ok": True}


def _decode_data_url(data_url):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)
