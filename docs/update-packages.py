#!/usr/bin/env python3
"""
通过 npm Registry API 查询 @ohos-ports scope 下所有已发布的包。

策略：
1. GET /-/user/ohos-ports/package — 一次请求获取全部包名列表
2. 并发 GET /@ohos-ports/<name> — 获取每个包的 packument（dist-tags + description）
3. 从 dist-tags 判断 stable/beta

输出：
- docs/data/packages.jsonl（每行一个包）
- docs/data/index.json（统计摘要）
- README.md ports 表格（仅 ports/ 目录下的包）

用法：python3 docs/update-packages.py
环境变量：
  NPM_REGISTRY - 默认 https://registry.npmjs.org
  NPM_ORG     - 默认 ohos-ports
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

REGISTRY = os.environ.get("NPM_REGISTRY", "https://registry.npmjs.org")
NPM_ORG = os.environ.get("NPM_ORG", "ohos-ports")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
PORTS_DIR = os.path.join(REPO_DIR, "ports")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
JSONL_PATH = os.path.join(DATA_DIR, "packages.jsonl")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")
README_PATH = os.path.join(REPO_DIR, "README.md")


def fetch_json(url, retries=3):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                print(f"  429, waiting {wait}s... ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Error: {e}, retrying... ({attempt+1}/{retries})")
                time.sleep(3)
                continue
            raise
    raise Exception(f"Failed after {retries} retries: {url}")


def get_ports_packages():
    ports = {}
    if not os.path.isdir(PORTS_DIR):
        return ports
    for name in sorted(os.listdir(PORTS_DIR)):
        path = os.path.join(PORTS_DIR, name)
        if not os.path.isdir(path):
            continue
        versions = sorted([d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))])
        if versions:
            ports[name] = versions[-1]
    return ports


def fetch_package_detail(name):
    """获取单个包的完整 packument，提取 dist-tags 和 description"""
    url = f"{REGISTRY}/{name}"
    try:
        data = fetch_json(url)
    except Exception as e:
        print(f"  Failed to fetch {name}: {e}")
        return None

    dist_tags = data.get("dist-tags", {})
    latest = dist_tags.get("latest")
    beta = dist_tags.get("beta")
    description = data.get("description", "")

    # 如果顶层 description 为空，从版本历史中查找（最新版可能漏设了 description）
    if not description:
        versions = data.get("versions", {})
        for ver_key in sorted(versions.keys(), reverse=True):
            ver_desc = versions[ver_key].get("description", "")
            if ver_desc:
                description = ver_desc
                break

    # 判断 stable/beta
    if beta:
        if "beta" in (latest or "").lower():
            stable_version = None
            beta_version = latest
        else:
            stable_version = latest
            beta_version = beta
    else:
        if latest and "beta" in latest.lower():
            stable_version = None
            beta_version = latest
        else:
            stable_version = latest
            beta_version = None

    return {
        "name": name,
        "stable_version": stable_version,
        "beta_version": beta_version,
        "description": description,
        "npm_url": f"https://www.npmjs.com/package/{name}",
    }


def search_packages():
    """查询 @ohos-ports 下所有包，返回完整数据列表"""
    # Step 1: 一次请求获取全部包名列表
    print(f"  Fetching package list from /-/user/{NPM_ORG}/package ...")
    url = f"{REGISTRY}/-/user/{NPM_ORG}/package"
    data = fetch_json(url)
    package_names = sorted(data.keys())
    print(f"  Found {len(package_names)} packages")

    # Step 2: 并发获取每个包的 packument
    print(f"  Fetching packuments (20 concurrent) ...")
    packages = []
    done = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_name = {executor.submit(fetch_package_detail, name): name for name in package_names}
        for future in as_completed(future_to_name):
            done += 1
            pkg = future.result()
            if pkg:
                packages.append(pkg)
            if done % 50 == 0:
                print(f"    Progress: {done}/{len(package_names)}")

    packages.sort(key=lambda x: x["name"])
    print(f"  Successfully fetched {len(packages)}/{len(package_names)} packages")
    return packages


def generate_output(packages, ports_map):
    """生成 packages.jsonl + index.json"""
    os.makedirs(DATA_DIR, exist_ok=True)

    result = []
    for p in packages:
        short_name = p["name"].replace("@ohos-ports/", "")
        is_ci = short_name in ports_map

        result.append({
            "name": p["name"],
            "short_name": short_name,
            "stable_version": p["stable_version"],
            "beta_version": p["beta_version"],
            "description": p["description"],
            "source": "CI发布" if is_ci else "本地发布",
            "npm_url": p["npm_url"],
        })

    result.sort(key=lambda x: (x["source"] != "CI发布", x["name"]))

    # packages.jsonl
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        for p in result:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # index.json
    ci_count = sum(1 for p in result if p["source"] == "CI发布")
    local_count = len(result) - ci_count
    index_data = {
        "total": len(result),
        "ci_count": ci_count,
        "local_count": local_count,
        "last_updated": str(date.today()),
    }
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    print(f"  Generated packages.jsonl: {len(result)} packages (CI: {ci_count}, local: {local_count})")
    print(f"  Generated index.json")
    return result


def update_readme(ports_map, packages):
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    lines = ["| 包名 | 版本 | 安装 |", "|------|------|------|"]
    for name in sorted(ports_map.keys()):
        npm_name = f"@ohos-ports/{name}"
        pkg = next((p for p in packages if p["name"] == npm_name), None)
        version = (pkg["stable_version"] if pkg else None) or (pkg["beta_version"] if pkg else None) or ports_map[name]
        lines.append(f"| {npm_name} | {version} | `npm i {npm_name}` |")

    new_table = "\n".join(lines)
    start_marker = "<!-- PORTS_TABLE_START -->"
    end_marker = "<!-- PORTS_TABLE_END -->"

    if start_marker in readme and end_marker in readme:
        pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
        readme = pattern.sub(f"{start_marker}\n{new_table}\n{end_marker}", readme)
    else:
        block = f"<!-- PORTS_TABLE_START -->\n{new_table}\n<!-- PORTS_TABLE_END -->\n\n"
        if "## 端到端运作流程" in readme:
            readme = readme.replace("## 端到端运作流程", block + "## 端到端运作流程")
        else:
            readme += "\n" + block

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(readme)
    print("  Updated README.md ports table")


def main():
    print(f"Registry: {REGISTRY}")
    ports_map = get_ports_packages()
    print(f"Ports: {len(ports_map)} - {list(ports_map.keys())}")

    print("\nSearching @ohos-ports packages...")
    packages = search_packages()

    print("\nGenerating output...")
    result = generate_output(packages, ports_map)
    update_readme(ports_map, packages)

    print(f"\nDone! {len(result)} packages")


if __name__ == "__main__":
    main()
