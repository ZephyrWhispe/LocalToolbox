"""图片上传桥接层。"""

import base64
import threading

from .base import BridgeBase


class UploadApi(BridgeBase):
    def _init_upload(self):
        from ..core.image_upload import ImageUploader
        self._uploader = ImageUploader()

    def _targets(self):
        """cfg 中的上传目标列表（非列表一律回退空）。"""
        t = self.cfg.get("upload_targets")
        return t if isinstance(t, list) else []

    def upload_image(self, data_url, provider="imgur"):
        """兼容旧接口：provider 为 imgur / custom / target:<index>。"""
        try:
            raw = _decode_data_url(data_url)
        except Exception as e:
            return {"ok": False, "err": "解码图片失败: %s" % e}

        if isinstance(provider, str) and provider.startswith("target:"):
            try:
                idx = int(provider.split(":", 1)[1])
                target = self._targets()[idx]
            except (ValueError, IndexError):
                return {"ok": False, "err": "上传目标不存在，请刷新配置"}
            return self._uploader.upload_to_target(raw, target)

        if provider == "custom":
            url = self.cfg.get("upload_custom_url", "")
            key = self.cfg.get("upload_custom_key", "")
            if not url:
                return {"ok": False, "err": "请先配置自定义上传地址"}
            result = self._uploader.upload_custom(raw, url, key)
        else:
            result = self._uploader.upload_imgur(raw)
        return result

    def upload_to(self, target_id, data_url):
        """按目标上传（v5.4 O2）：target_id ∈ imgur / custom / target:<index>。"""
        return self.upload_image(data_url, str(target_id or "imgur"))

    def upload_targets_list(self):
        """上传目标列表。"""
        return {"ok": True, "data": self._targets()}

    def upload_targets_add(self, target):
        """新增上传目标（≤10 个），传 dict 配置。"""
        from ..core.image_upload import validate_target
        try:
            item = validate_target(target)
        except ValueError as e:
            return {"ok": False, "err": str(e)}
        targets = self._targets()
        if len(targets) >= 10:
            return {"ok": False, "err": "上传目标最多 10 个，请先删除不再使用的目标"}
        targets.append(item)
        self.cfg.set("upload_targets", targets)
        return {"ok": True, "data": targets}

    def upload_targets_del(self, index):
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "err": "目标序号不合法"}
        targets = self._targets()
        if idx < 0 or idx >= len(targets):
            return {"ok": False, "err": "目标不存在，请刷新列表"}
        removed = targets.pop(idx)
        self.cfg.set("upload_targets", targets)
        return {"ok": True, "data": {"removed": removed.get("name", ""), "targets": targets}}

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
