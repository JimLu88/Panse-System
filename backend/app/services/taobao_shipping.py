"""TOP logistics client. Restrict full-order shipment to an exact single purchased unit."""
import hashlib
import hmac
import os
from datetime import datetime, timezone, timedelta

import httpx


class ShippingGate(ValueError):
    """Business or platform gate before submission. Safe for an operator to read."""


class TaobaoShipping:
    def __init__(self):
        self.key = os.getenv("PANSE_TOP_APP_KEY")
        self.secret = os.getenv("PANSE_TOP_APP_SECRET")
        self.session = os.getenv("PANSE_TOP_SELLER_SESSION")
        self.seller = os.getenv("PANSE_TOP_SELLER_NICK")
        self.shop = os.getenv("PANSE_TOP_ERP_SHOP")
        if not all((self.key, self.secret, self.session, self.seller, self.shop)):
            raise ShippingGate("店铺的淘宝发货接口尚未配置")

    def call(self, method, **params):
        payload = {**params, "method": method, "app_key": self.key, "session": self.session,
                   "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
                   "format": "json", "v": "2.0", "sign_method": "hmac"}
        raw = "".join(k + str(payload[k]) for k in sorted(payload)).encode()
        payload["sign"] = hmac.new(self.secret.encode(), raw, hashlib.md5).hexdigest().upper()
        response = httpx.post("https://eco.taobao.com/router/rest", data=payload, timeout=15)
        response.raise_for_status()
        reply = response.json().get(method.removeprefix("taobao.").replace(".", "_") + "_response")
        if not isinstance(reply, dict):
            raise ShippingGate("淘宝接口未返回可核验结果，需核对店铺授权或平台记录")
        return reply

    def trade(self, order_no):
        return self.call("taobao.trade.fullinfo.get", tid=order_no,
            fields="tid,seller_nick,status,orders.oid,orders.status,orders.num,orders.refund_status").get("trade", {})

    def exact_trade(self, row, expected):
        trade = self.trade(row.order_no)
        lines = trade.get("orders", {}).get("order", [])
        return (str(trade.get("tid")) == row.order_no and trade.get("seller_nick") == self.seller
                and trade.get("status") == expected and len(lines) == 1
                and str(lines[0].get("oid")) == row.sub_order_no and str(lines[0].get("num")) == "1"
                and lines[0].get("status") == expected and lines[0].get("refund_status") == "NO_REFUND")

    def carrier_code(self, name):
        response = self.call("taobao.logistics.companies.get", fields="code,name")
        rows = response.get("logistics_companies", {}).get("logistics_company", [])
        codes = {c["code"] for c in rows if c.get("name") == name and c.get("code")}
        if len(codes) != 1 or "OTHER" in codes:
            raise ShippingGate("物流公司名称与淘宝承运商未唯一匹配")
        return codes.pop()

    def send(self, row, code):
        return self.call("taobao.logistics.offline.send", tid=row.order_no, company_code=code,
                         out_sid=row.tracking_no)

    def confirmed(self, row):
        if not self.exact_trade(row, "WAIT_BUYER_CONFIRM_GOODS"):
            return False
        result = self.call("taobao.logistics.orders.detail.get", tid=row.order_no,
                           fields="tid,out_sid,company_name,seller_confirm")
        ships = result.get("shippings", {}).get("shipping", [])
        return (len(ships) == 1 and str(ships[0].get("tid")) == row.order_no
                and ships[0].get("out_sid") == row.tracking_no and ships[0].get("company_name") == row.carrier
                and str(ships[0].get("seller_confirm")).lower() == "true")
