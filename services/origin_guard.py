"""本机服务的来源校验(http 中间件与 WebSocket 共用)。"""
from __future__ import annotations
import ipaddress

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TEST_HOSTS = {"testserver"}      # TestClient 默认 Host; 没有顶级域, 公网注册不到, 重绑定用不了


def host_ok(hostname: str) -> bool:
    """Host 只认本机名或 IP 字面量(局域网里用 IP 访问)。外部域名哪怕解析到 127.0.0.1
    也拒绝: 这是 DNS 重绑定, 攻击页面与本服务同源, Origin 检查拦不住, 连读数据都能做到。"""
    h = (hostname or "").strip("[]").lower()
    if h in LOCAL_HOSTS or h in TEST_HOSTS:
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def ws_allowed(host: str, origin: str | None) -> bool:
    """WebSocket 握手: Host 是本机名/IP, 且 Origin(若有)是同一主机或本机名。"""
    from urllib.parse import urlsplit
    if not host_ok(urlsplit("//" + (host or "")).hostname or ""):
        return False
    if not origin:
        return True
    o = urlsplit(origin)
    return o.netloc == host or (o.hostname or "") in LOCAL_HOSTS
