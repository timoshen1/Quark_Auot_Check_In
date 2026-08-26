"""Quark cloud-drive daily sign-in."""

from __future__ import annotations

import os
import re
import sys
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests


INFO_URL = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/info"
SIGN_URL = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/sign"
REQUIRED_PARAMS = ("kps", "sign", "vcode")


class ConfigError(ValueError):
    pass

class QuarkAPIError(RuntimeError):
    pass


def send(title: str, message: str) -> None:
    print(f"{title}:\n{message}")


def split_account_entries(raw_value: str | None) -> list[str]:
    if not raw_value or not raw_value.strip():
        raise ConfigError("未配置 COOKIE_QUARK，或变量内容为空")
    entries = [e.strip() for e in re.split(r"\r?\n|&&", raw_value)]
    entries = [e for e in entries if e]
    if not entries:
        raise ConfigError("COOKIE_QUARK 中没有可用的账号配置")
    return entries


def extract_params(url: str) -> dict[str, str]:
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    return {n: query.get(n, [""])[0] for n in REQUIRED_PARAMS}


def parse_account(entry: str, index: int) -> dict[str, str]:
    account: dict[str, str] = {}
    for field in entry.split(";"):
        field = field.strip()
        if not field:
            continue
        if "=" not in field:
            raise ConfigError(f"第 {index} 个账号存在无效字段：{field}")
        k, v = field.split("=", 1)
        k = k.strip()
        if not k:
            raise ConfigError(f"第 {index} 个账号存在空字段名")
        account[k] = v.strip()
    if "url" in account:
        for k, v in extract_params(account["url"]).items():
            account.setdefault(k, v)
    missing = [k for k in REQUIRED_PARAMS if not account.get(k)]
    if missing:
        raise ConfigError(f"第 {index} 个账号缺少必要参数：{', '.join(missing)}")
    account.setdefault("user", f"账号{index}")
    return account


def _api_error_message(payload: dict, fallback: str) -> str:
    msg = payload.get("message") or payload.get("msg")
    code = payload.get("code")
    if msg:
        return str(msg)
    if code is not None:
        return f"{fallback}（code={code}）"
    return fallback


class Quark:
    def __init__(self, account: dict, session=None, timeout=20):
        self.account = account
        self.session = session or requests.Session()
        self.timeout = timeout

    @staticmethod
    def convert_bytes(v):
        units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        s, i = float(v), 0
        while s >= 1024 and i < len(units) - 1:
            s /= 1024
            i += 1
        return f"{s:.2f} {units[i]}"

    def _params(self):
        return {"pr": "ucpro", "fr": "android", **{k: self.account[k] for k in REQUIRED_PARAMS}}

    def _request(self, method, url, **kwargs):
        try:
            r = self.session.request(method, url, timeout=self.timeout, **kwargs)
            r.raise_for_status()
            p = r.json()
        except requests.Timeout as e:
            raise QuarkAPIError("请求夸克接口超时") from e
        except requests.RequestException as e:
            s = getattr(getattr(e, "response", None), "status_code", None)
            d = f"HTTP {s}" if s else type(e).__name__
            raise QuarkAPIError(f"请求夸克接口失败（{d}）") from e
        except ValueError as e:
            raise QuarkAPIError("夸克接口返回了无法解析的数据") from e
        if not isinstance(p, dict):
            raise QuarkAPIError("夸克接口返回格式异常")
        return p

    def get_growth_info(self):
        p = self._request("GET", INFO_URL, params=self._params())
        d = p.get("data")
        if not isinstance(d, dict):
            raise QuarkAPIError(_api_error_message(p, "获取成长信息失败"))
        return d

    def get_growth_sign(self):
        p = self._request("POST", SIGN_URL, params=self._params(), json={"sign_cyclic": True})
        d = p.get("data")
        if not isinstance(d, dict) or "sign_daily_reward" not in d:
            raise QuarkAPIError(_api_error_message(p, "签到失败"))
        return d["sign_daily_reward"]

    def do_sign(self):
        info = self.get_growth_info()
        cs = info.get("cap_sign")
        if not isinstance(cs, dict):
            raise QuarkAPIError("成长信息中缺少 cap_sign 字段")
        user = self.account["user"]
        vip = "88VIP" if info.get("88VIP") else "普通用户"
        total = info.get("total_capacity", 0)
        comp = info.get("cap_composition") or {}
        acc = comp.get("sign_reward", 0)
        prog = cs.get("sign_progress", "?")
        tgt = cs.get("sign_target", "?")
        lines = [f"{vip} {user}", f"💾 总容量：{self.convert_bytes(total)}，累计：{self.convert_bytes(acc)}"]
        if cs.get("sign_daily"):
            rew = cs.get("sign_daily_reward", 0)
            lines.append(f"✅ 今日已签到 +{self.convert_bytes(rew)}，连签（{prog}/{tgt}）")
        else:
            rew = self.get_growth_sign()
            np_ = prog + 1 if isinstance(prog, int) else "?"
            lines.append(f"✅ 签到成功 +{self.convert_bytes(rew)}，连签（{np_}/{tgt}）")
        return "\n".join(lines)


def main(cookie_value=None):
    print("----------夸克网盘开始签到----------")
    if cookie_value is None:
        cookie_value = os.getenv("COOKIE_QUARK")
    try:
        entries = split_account_entries(cookie_value)
    except ConfigError as e:
        print(f"❌ {e}")
        return 2
    print(f"✅ 检测到共 {len(entries)} 个账号\n")
    results, failures = [], 0
    for i, entry in enumerate(entries, 1):
        head = f"🙍🏻‍♂️ 第 {i} 个账号"
        try:
            acc = parse_account(entry, i)
            results.append(f"{head}\n{Quark(acc).do_sign()}")
        except Exception as e:
            failures += 1
            results.append(f"{head}\n❌ {e}")
    summary = "\n\n".join(results)
    send("夸克自动签到", summary)

    # 输出给 GitHub Actions
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("log<<GITHUB_EOF\n")
            f.write(summary)
            f.write("\nGITHUB_EOF\n")

    print(f"\n----------完毕：成功 {len(entries)-failures}，失败 {failures}----------")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
