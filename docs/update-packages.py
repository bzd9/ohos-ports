#!/usr/bin/env python3
"""
通过 npm Registry API 查询 @ohos-ports scope 下所有已发布的包。

功能：
1. 生成 docs/data/packages.json（所有包，含正式+Beta）
2. 更新 README.md 中的 ports 包列表表格（仅 ports/ 目录下的包）

用法：python3 docs/update-packages.py
环境变量：
  NPM_REGISTRY   - 默认 https://registry.npmmirror.com
  NPM_AUTH_TOKEN - npm access token，用于 Org API（可选）
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import date

REGISTRY = os.environ.get("NPM_REGISTRY", "https://registry.npmmirror.com")
NPM_AUTH_TOKEN = os.environ.get("NPM_AUTH_TOKEN", "")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
PORTS_DIR = os.path.join(REPO_DIR, "ports")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "data", "packages.json")
README_PATH = os.path.join(REPO_DIR, "README.md")


def fetch_json(url, headers=None, retries=3):
    """发送 HTTP 请求，返回 JSON，带重试"""
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                print(f"    429 Too Many Requests, waiting {wait}s... (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                print(f"    Error: {e}, retrying... (attempt {attempt+1}/{retries})")
                time.sleep(3)
                continue
            raise
    raise Exception(f"Failed after {retries} retries: {url}")


def get_ports_packages():
    """扫描 ports/ 目录，返回 {包名: 版本} 字典"""
    ports = {}
    if not os.path.isdir(PORTS_DIR):
        return ports
    for name in sorted(os.listdir(PORTS_DIR)):
        port_path = os.path.join(PORTS_DIR, name)
        if not os.path.isdir(port_path):
            continue
        versions = sorted([d for d in os.listdir(port_path) if os.path.isdir(os.path.join(port_path, d))])
        if versions:
            ports[name] = versions[-1]
    return ports


def get_package_names():
    """获取所有 @ohos-ports 包名"""
    # 方式1：尝试 Org API（需要 token）
    if NPM_AUTH_TOKEN:
        try:
            print("Trying Org API...")
            url = f"{REGISTRY}/-/org/ohos-ports/packages"
            headers = {"Accept": "application/json", "Authorization": f"Bearer {NPM_AUTH_TOKEN}"}
            data = fetch_json(url, headers)
            names = [n for n in data.keys() if n.startswith("@ohos-ports/")]
            print(f"  Org API returned {len(names)} packages")
            if names:
                return sorted(names)
        except Exception as e:
            print(f"  Org API failed: {e}, falling back to Search API")

    # 方式2：Search API（公开）
    print("Using Search API...")
    all_names = []
    page = 0
    while True:
        url = f"{REGISTRY}/-/v1/search?text=%40ohos-ports&size=250&from={page * 250}"
        print(f"  Search page {page}...")
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  Search failed: {e}")
            break
        objects = data.get("objects", [])
        for obj in objects:
            name = obj["package"]["name"]
            if name.startswith("@ohos-ports/") and name not in all_names:
                all_names.append(name)
        if len(all_names) >= data.get("total", 0) or not objects:
            break
        page += 1
        time.sleep(1)  # 避免速率限制

    print(f"  Search found {len(all_names)} packages")
    return sorted(all_names)


def query_package_detail(name, index, total):
    """查询单个包：获取正式版本、Beta版本、说明"""
    url = f"{REGISTRY}/{name.replace('/', '%2F')}"
    try:
        data = fetch_json(url, retries=2)
    except urllib.error.HTTPError as e:
        print(f"  [{index}/{total}] {name}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"  [{index}/{total}] {name}: Error: {e}")
        return None

    dist_tags = data.get("dist-tags", {})
    versions = data.get("versions", {})
    latest = dist_tags.get("latest", "")
    beta = dist_tags.get("beta")

    latest_stable = None
    latest_beta = None
    for v in sorted(versions.keys(), reverse=True):
        if "beta" in v.lower():
            if latest_beta is None:
                latest_beta = v
        else:
            if latest_stable is None:
                latest_stable = v

    if latest and "beta" not in latest.lower():
        latest_stable = latest
    if beta:
        latest_beta = beta

    latest_data = versions.get(latest_stable or latest or "", {})
    description = latest_data.get("description", "") or ""

    print(f"  [{index}/{total}] {name}: stable={latest_stable or '-'}, beta={latest_beta or '-'}")
    return {
        "name": name,
        "stable_version": latest_stable,
        "beta_version": latest_beta,
        "description": description,
        "npm_url": f"https://www.npmjs.com/package/{name}",
    }


def generate_packages_json(packages, ports_map):
    """生成 packages.json"""
    result_packages = []
    for p in packages:
        short_name = p["name"].replace("@ohos-ports/", "")
        is_ci = short_name in ports_map

        desc = p["description"]
        if p["stable_version"] and p["beta_version"] and p["stable_version"] == p["beta_version"]:
            desc = f"（仅 Beta 发布）{desc}"

        result_packages.append({
            "name": p["name"],
            "short_name": short_name,
            "stable_version": p["stable_version"],
            "beta_version": p["beta_version"],
            "description": desc,
            "source": "CI发布" if is_ci else "本地发布",
            "npm_url": p["npm_url"],
        })

    result_packages.sort(key=lambda x: (x["source"] != "CI发布", x["name"]))

    ci_count = sum(1 for p in result_packages if p["source"] == "CI发布")
    local_count = len(result_packages) - ci_count

    result = {
        "last_updated": str(date.today()),
        "total": len(result_packages),
        "ci_count": ci_count,
        "local_count": local_count,
        "packages": result_packages
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nGenerated packages.json: {len(result_packages)} packages (CI: {ci_count}, local: {local_count})")
    return result


def update_readme_ports_table(ports_map, packages):
    """更新 README.md 中的 ports 包列表表格"""
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    lines = ["| 包名 | 版本 | 安装 |", "|------|------|------|"]
    for name in sorted(ports_map.keys()):
        npm_name = f"@ohos-ports/{name}"
        pkg = next((p for p in packages if p["name"] == npm_name), None)
        version = pkg["stable_version"] or pkg["beta_version"] or ports_map[name]
        lines.append(f"| {npm_name} | {version} | `npm i {npm_name}` |")

    new_table = "\n".join(lines)

    start_marker = "<!-- PORTS_TABLE_START -->"
    end_marker = "<!-- PORTS_TABLE_END -->"

    if start_marker in readme and end_marker in readme:
        pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
        readme = pattern.sub(f"{start_marker}\n{new_table}\n{end_marker}", readme)
    else:
        marker_block = f"<!-- PORTS_TABLE_START -->\n{new_table}\n<!-- PORTS_TABLE_END -->\n\n"
        if "## 端到端运作流程" in readme:
            readme = readme.replace("## 端到端运作流程", marker_block + "## 端到端运作流程")
        else:
            readme += "\n" + marker_block

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(readme)
    print("Updated README.md ports table")


def main():
    print(f"Registry: {REGISTRY}")
    print(f"Auth token: {'Yes' if NPM_AUTH_TOKEN else 'No'}")

    ports_map = get_ports_packages()
    print(f"Ports directory: {len(ports_map)} packages - {list(ports_map.keys())}")

    all_names = get_package_names()

    print(f"\nQuerying {len(all_names)} packages...")
    packages = []
    for i, name in enumerate(all_names):
        detail = query_package_detail(name, i + 1, len(all_names))
        if detail:
            packages.append(detail)
        time.sleep(0.3)  # 避免速率限制

    generate_packages_json(packages, ports_map)
    update_readme_ports_table(ports_map, packages)

    print("\nDone!")


if __name__ == "__main__":
    main()
