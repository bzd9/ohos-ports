#!/usr/bin/env python3
"""
通过 npm Registry API 查询 @ohos-ports scope 下所有已发布的包。

功能：
1. 生成 docs/data/packages.json（所有包，含正式+Beta）
2. 更新 README.md 中的 ports 包列表表格（仅 ports/ 目录下的包）

优先使用 Org API（需 NPM_AUTH_TOKEN），降级使用 Search API。
用法：python3 docs/update-packages.py
环境变量：
  NPM_REGISTRY   - 默认 https://registry.npmmirror.com
  NPM_AUTH_TOKEN - npm access token，用于 Org API
"""

import json
import os
import re
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


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


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
            ports[name] = versions[-1]  # 取最后一个版本
    return ports


def get_package_names():
    """获取所有 @ohos-ports 包名"""
    if NPM_AUTH_TOKEN:
        try:
            print("Using Org API...")
            url = f"{REGISTRY}/-/org/ohos-ports/packages"
            headers = {"Accept": "application/json", "Authorization": f"Bearer {NPM_AUTH_TOKEN}"}
            data = fetch_json(url, headers)
            names = [n for n in data.keys() if n.startswith("@ohos-ports/")]
            print(f"  Org API returned {len(names)} packages")
            if names:
                return sorted(names)
        except Exception as e:
            print(f"  Org API failed: {e}")

    print("Using Search API...")
    all_names = []
    page = 0
    while True:
        url = f"{REGISTRY}/-/v1/search?text=%40ohos-ports&size=250&from={page * 250}"
        data = fetch_json(url)
        objects = data.get("objects", [])
        for obj in objects:
            name = obj["package"]["name"]
            if name.startswith("@ohos-ports/") and name not in all_names:
                all_names.append(name)
        if len(all_names) >= data.get("total", 0) or not objects:
            break
        page += 1
    print(f"  Search API found {len(all_names)} packages")
    return sorted(all_names)


def query_package_detail(name):
    """查询单个包：获取正式版本、Beta版本、说明"""
    url = f"{REGISTRY}/{name.replace('/', '%2F')}"
    try:
        data = fetch_json(url)
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"    Error: {e}")
        return None

    dist_tags = data.get("dist-tags", {})
    versions = data.get("versions", {})
    latest = dist_tags.get("latest", "")
    beta = dist_tags.get("beta")

    # 从版本列表中判断 stable/beta
    latest_stable = None
    latest_beta = None
    for v in sorted(versions.keys(), reverse=True):
        if "beta" in v.lower():
            if latest_beta is None:
                latest_beta = v
        else:
            if latest_stable is None:
                latest_stable = v

    # 优先用 dist-tags
    if latest and "beta" not in latest.lower():
        latest_stable = latest
    if beta:
        latest_beta = beta

    latest_data = versions.get(latest_stable or latest or "", {})
    description = latest_data.get("description", "") or ""

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

        # 判断说明：正式版本与Beta版本相同 → 仅发布了Beta
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

    print(f"Generated packages.json: {len(result_packages)} packages (CI: {ci_count}, local: {local_count})")
    return result


def update_readme_ports_table(ports_map, packages):
    """更新 README.md 中的 ports 包列表表格"""
    # 读取当前 README
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    # 生成表格
    lines = ["| 包名 | 版本 | 安装 |", "|------|------|------|"]
    for name in sorted(ports_map.keys()):
        npm_name = f"@ohos-ports/{name}"
        # 从 packages 中找到对应的版本
        pkg = next((p for p in packages if p["name"] == npm_name), None)
        version = pkg["stable_version"] or pkg["beta_version"] or ports_map[name]
        lines.append(f"| {npm_name} | {version} | `npm i {npm_name}` |")

    new_table = "\n".join(lines)

    # 用标记替换 README 中的表格区域
    start_marker = "<!-- PORTS_TABLE_START -->"
    end_marker = "<!-- PORTS_TABLE_END -->"

    if start_marker in readme and end_marker in readme:
        # 替换标记之间的内容
        pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
        readme = pattern.sub(f"{start_marker}\n{new_table}\n{end_marker}", readme)
    else:
        # 首次：在 "## 端到端运作流程" 前插入
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

    # 1. 扫描 ports/ 目录
    ports_map = get_ports_packages()
    print(f"Ports directory: {len(ports_map)} packages - {list(ports_map.keys())}")

    # 2. 获取所有 @ohos-ports 包名
    all_names = get_package_names()

    # 3. 查询每个包的详细信息
    print(f"\nQuerying {len(all_names)} packages...")
    packages = []
    for i, name in enumerate(all_names):
        print(f"  [{i+1}/{len(all_names)}] {name}")
        detail = query_package_detail(name)
        if detail:
            packages.append(detail)

    # 4. 生成 packages.json
    generate_packages_json(packages, ports_map)

    # 5. 更新 README.md ports 表格
    update_readme_ports_table(ports_map, packages)

    print("\nDone!")


if __name__ == "__main__":
    main()
