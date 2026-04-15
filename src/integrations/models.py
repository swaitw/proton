from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

ChannelName = Literal["telegram", "dingtalk", "weixin", "feishu"]


class PortalChannelBinding(BaseModel):
    portal_id: str
    channel: ChannelName
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)
    state: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PortalChannelStatus(BaseModel):
    portal_id: str
    channel: ChannelName
    enabled: bool
    connected: bool
    last_error: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class WeixinQrStartResponse(BaseModel):
    login_id: str
    qrcode: str
    qrcode_img_content: str = ""
    status: str = "wait"


class WeixinQrStatusResponse(BaseModel):
    login_id: str
    status: str
    credential: Optional[Dict[str, str]] = None

