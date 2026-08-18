#!/usr/bin/env python3
"""
通过 npm Registry API 查询 @ohos-ports scope 下所有已发布的包。

策略：
1. GET /-/user/ohos-ports/package — 一次请求获取全部包名列表
2. 并发 GET /@ohos-ports/<name> — 获取每个包的 packument（dist-tags + description + deprecated）
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


def package_exists(name, registry=REGISTRY):
    """快速检查包是否在 npm registry 上存在（404 即不存在）"""
    url = f"{registry}/{name}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def find_original_name(short_name, packument_data):
    """从 packument 数据和 npm registry 查询中推断原始包名

    策略：
    1. 检查 repository.directory 中的 @scope/name 模式
    2. 尝试 unscoped short_name
    3. 尝试 scoped 变体（在 dash 位置拆分）
    4. fallback 到 short_name
    """
    # Step 1: 检查 repository.directory
    dist_tags = packument_data.get("dist-tags", {})
    latest = dist_tags.get("latest") or dist_tags.get("beta")
    versions = packument_data.get("versions", {})
    latest_info = versions.get(latest, {}) if latest else {}
    repo = latest_info.get("repository", {})
    repo_dir = repo.get("directory", "") if isinstance(repo, dict) else ""
    if repo_dir:
        match = re.search(r"(@[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*)", repo_dir, re.I)
        if match:
            return match.group(1)

    # Step 2: 尝试 unscoped short_name
    if package_exists(short_name):
        return short_name

    # Step 3: 尝试 scoped 变体
    parts = short_name.split("-")
    if len(parts) > 1:
        for i in range(1, len(parts)):
            scope = "-".join(parts[:i])
            pkg = "-".join(parts[i:])
            scoped = f"@{scope}/{pkg}"
            if package_exists(scoped):
                return scoped

    # Step 4: fallback
    return short_name


def _is_newer(a, b):
    """判断版本 a 是否比 b 更新

    通过逐段比较版本号组件，数字按数值比较，非数字按字符串比较。
    例: _is_newer("10.5.8-beta.3", "10.5.8-beta.1") → True
    """
    pa = re.split(r"[.\-]", a or "")
    pb = re.split(r"[.\-]", b or "")
    for i in range(max(len(pa), len(pb))):
        va = pa[i] if i < len(pa) else "0"
        vb = pb[i] if i < len(pb) else "0"
        try:
            na, nb = int(va), int(vb)
            if na > nb:
                return True
            if na < nb:
                return False
        except ValueError:
            if va > vb:
                return True
            if va < vb:
                return False
    return False


def fetch_package_detail(name, cache=None):
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
            # latest 和 beta tag 都是 beta 版本，取更新的
            beta_version = latest if _is_newer(latest, beta) else beta
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

    # 检查最新版是否被废弃
    versions = data.get("versions", {})
    latest_info = versions.get(latest, {}) if latest else {}
    deprecated_msg = latest_info.get("deprecated", "")

    # 查找原始包名：优先使用缓存，仅对新增包查询 npm
    short_name = name.replace("@ohos-ports/", "")
    cached = cache.get(name) if cache else None
    if cached:
        original_name = cached["original_name"]
        original_npm_url = cached.get("original_npm_url") or f"https://www.npmjs.com/package/{original_name}"
    else:
        original_name = find_original_name(short_name, data)
        original_npm_url = f"https://www.npmjs.com/package/{original_name}"

    return {
        "name": name,
        "short_name": short_name,
        "original_name": original_name,
        "original_npm_url": original_npm_url,
        "stable_version": stable_version,
        "beta_version": beta_version,
        "description": description,
        "deprecated": bool(deprecated_msg),
        "deprecated_message": deprecated_msg or None,
        "npm_url": f"https://www.npmjs.com/package/{name}",
    }


def load_original_name_cache():
    """从现有 packages.jsonl 加载已解析的原始包名缓存，避免重复查询 npm"""
    cache = {}
    if not os.path.exists(JSONL_PATH):
        return cache
    with open(JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                if p.get("original_name"):
                    cache[p["name"]] = {
                        "original_name": p["original_name"],
                        "original_npm_url": p.get("original_npm_url"),
                    }
            except Exception:
                pass
    return cache


def search_packages(cache=None):
    """查询 @ohos-ports 下所有包，返回完整数据列表"""
    # Step 1: 一次请求获取全部包名列表
    print(f"  Fetching package list from /-/user/{NPM_ORG}/package ...")
    url = f"{REGISTRY}/-/user/{NPM_ORG}/package"
    data = fetch_json(url)
    package_names = sorted(data.keys())
    print(f"  Found {len(package_names)} packages")

    # 统计缓存命中情况
    cached_count = sum(1 for n in package_names if cache and n in cache)
    new_count = len(package_names) - cached_count
    print(f"  Cache: {cached_count} cached, {new_count} new (will query npm for original_name)")

    # Step 2: 并发获取每个包的 packument
    print(f"  Fetching packuments (20 concurrent) ...")
    packages = []
    done = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_name = {executor.submit(fetch_package_detail, name, cache): name for name in package_names}
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


PLATFORM_SUFFIXES = [
    "-openharmony-arm64",
    "-openharmony-arm",
    "-openharmony",
]


def _try_find_scoped_parent(parent_short, suffix):
    """尝试从 npm 查询父包的 scoped 名称，返回拼接后的平台子包名称

    例如 parent_short='supabase-cli', suffix='-openharmony-arm64'
    若 npm 上存在 @supabase/cli，则返回 @supabase/cli-openharmony-arm64
    """
    parts = parent_short.split("-")
    if len(parts) <= 1:
        return None
    for i in range(1, len(parts)):
        scope = "-".join(parts[:i])
        name = "-".join(parts[i:])
        scoped = f"@{scope}/{name}"
        if package_exists(scoped):
            return f"{scoped}{suffix}"
    return None


def infer_platform_original_names(packages):
    """后处理：从已解析的父包推断平台子包的原始包名

    对于 original_name == short_name 的平台子包（如 xxx-openharmony-arm64），
    检查是否存在已解析的父包（如 xxx），若父包解析为 scoped 名称（如 @scope/xxx），
    则将相同的 scope 应用于平台子包（如 @scope/xxx-openharmony-arm64）。
    """
    # 构建 short_name -> original_name 映射（仅 scoped 的）
    scope_map = {}
    # 同时构建 short_name 集合，用于判断父包是否在列表中
    all_short_names = set()
    for p in packages:
        orig = p.get("original_name", "")
        short = p.get("short_name", "")
        all_short_names.add(short)
        if orig.startswith("@") and orig != short:
            scope_map[short] = orig

    fixed = 0
    for p in packages:
        orig = p.get("original_name", "")
        short = p.get("short_name", "")
        if orig != short:
            continue  # 已解析，跳过

        for suf in PLATFORM_SUFFIXES:
            if not short.endswith(suf):
                continue
            parent_short = short[: -len(suf)]

            if parent_short in scope_map:
                # 父包在列表中且已解析为 scoped
                parent_orig = scope_map[parent_short]
                slash_idx = parent_orig.index("/")
                scope = parent_orig[:slash_idx]
                parent_name = parent_orig[slash_idx + 1 :]
                inferred = f"{scope}/{parent_name}{suf}"
            elif parent_short not in all_short_names:
                # 父包不在列表中，尝试从 npm 查询父包的 scope
                inferred = _try_find_scoped_parent(parent_short, suf)
                if not inferred:
                    break  # 无法推断，保持 fallback
            else:
                # 父包在列表中但是 unscoped，平台子包也应是 unscoped
                break

            p["original_name"] = inferred
            p["original_npm_url"] = f"https://www.npmjs.com/package/{inferred}"
            fixed += 1
            break
    if fixed:
        print(f"  Inferred {fixed} platform package original names from parents")
    return packages


def generate_output(packages, ports_map):
    """生成 packages.jsonl + index.json，数据无变化时跳过写入"""
    os.makedirs(DATA_DIR, exist_ok=True)

    result = []
    for p in packages:
        short_name = p.get("short_name") or p["name"].replace("@ohos-ports/", "")
        is_ci = short_name in ports_map

        entry = {
            "name": p["name"],
            "short_name": short_name,
            "original_name": p.get("original_name", short_name),
            "original_npm_url": p.get("original_npm_url"),
            "stable_version": p["stable_version"],
            "beta_version": p["beta_version"],
            "description": p["description"],
            "deprecated": p["deprecated"],
            "deprecated_message": p["deprecated_message"],
            "source": "CI发布" if is_ci else "本地发布",
            "npm_url": p["npm_url"],
        }
        result.append(entry)

    result.sort(key=lambda x: (x["source"] != "CI发布", x["name"]))

    # 后处理：从已解析的父包推断平台子包的原始包名
    result = infer_platform_original_names(result)

    # 生成新内容，与旧文件比对
    new_jsonl = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in result)
    old_jsonl = ""
    if os.path.exists(JSONL_PATH):
        with open(JSONL_PATH, "r", encoding="utf-8") as f:
            old_jsonl = f.read()

    if new_jsonl == old_jsonl:
        print("  No changes, skipping write")
        return result

    # 数据有变化才写入
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        f.write(new_jsonl)

    ci_count = sum(1 for p in result if p["source"] == "CI发布")
    local_count = len(result) - ci_count
    deprecated_count = sum(1 for p in result if p["deprecated"])
    index_data = {
        "total": len(result),
        "ci_count": ci_count,
        "local_count": local_count,
        "deprecated_count": deprecated_count,
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

    print("\nLoading original_name cache...")
    cache = load_original_name_cache()
    print(f"  Cached: {len(cache)} packages")

    print("\nSearching @ohos-ports packages...")
    packages = search_packages(cache)

    print("\nGenerating output...")
    result = generate_output(packages, ports_map)
    update_readme(ports_map, packages)

    print(f"\nDone! {len(result)} packages")


if __name__ == "__main__":
    main()
