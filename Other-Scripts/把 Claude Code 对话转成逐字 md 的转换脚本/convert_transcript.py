# -*- coding: utf-8 -*-
"""
把 Claude Code 会话的 JSONL 转录文件转换成逐字 Markdown 对话记录。

用法：python convert_transcript.py <transcript.jsonl> [输出.md]
transcript.jsonl 来自会话导出的 zip 包（解压后根目录里的那个文件）。
"""
import json
import re
import sys
from collections import Counter

if len(sys.argv) < 2:
    sys.exit("用法：python convert_transcript.py <transcript.jsonl> [输出.md]")
SRC = sys.argv[1]
DST = sys.argv[2] if len(sys.argv) > 2 else "会话记录_逐字.md"

TITLE = "# 会话逐字记录：GPT Image 2.5 头像立绘生成"
INTRO = (
    "本记录由会话转录文件自动生成：用户与 Claude 的文字逐字保留；"
    "系统注入内容、思考过程和工具返回结果已省略；工具调用以单行摘要表示；"
    "项目内文件路径已改为相对路径。"
)

stats = Counter()


def shorten(s, n=80):
    if s is None:
        s = ""
    s = " ".join(str(s).split())
    if len(s) > n:
        s = s[: n - 1].rstrip() + "…"
    return s


def get_text(content):
    """从 tool_result / message content 中提取纯文本"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "tool_reference":
                    parts.append("[tool: %s]" % b.get("tool_name"))
                elif b.get("type") == "image":
                    parts.append("[图片]")
        return "\n".join(parts)
    return str(content)


PASTED_RE = re.compile(r'<pasted_content[^>]*>(.*?)</pasted_content[^>]*>', re.DOTALL)
SYS_REMINDER_RE = re.compile(r'<system-reminder>.*?</system-reminder>', re.DOTALL)
TASK_NOTIF_RE = re.compile(r'<task-notification>.*?</task-notification>', re.DOTALL)
SYS_NOTIF_RE = re.compile(r'\[SYSTEM NOTIFICATION.*?(?:\n\n|$)', re.DOTALL)
TOOL_LOADED_RE = re.compile(r'^\s*Tool loaded\.\s*$', re.MULTILINE)


def pasted_to_blockquote(m):
    inner = m.group(1).strip("\n")
    lines = inner.split("\n")
    return "\n".join("> " + ln for ln in lines)


def process_user_text(text):
    """去掉用户消息里的注入内容，pasted_content 转成引用块"""
    text = PASTED_RE.sub(pasted_to_blockquote, text)
    text = SYS_REMINDER_RE.sub("", text)
    text = TASK_NOTIF_RE.sub("", text)
    text = SYS_NOTIF_RE.sub("", text)
    text = TOOL_LOADED_RE.sub("", text)
    return text.strip("\n")


def tool_summary(name, inp):
    inp = inp or {}
    if name == "Agent":
        return inp.get("description", "")
    if name in ("Bash", "PowerShell"):
        desc = inp.get("description")
        if desc:
            return desc
        return shorten(inp.get("command", ""))
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        return inp.get("file_path", "")
    if name == "WebSearch":
        return inp.get("query", "")
    if name == "WebFetch":
        url = inp.get("url", "")
        prompt = inp.get("prompt", "")
        return shorten((url + "  " + prompt).strip())
    if name == "ToolSearch":
        return inp.get("query", "")
    if name == "SendUserFile":
        files = inp.get("files", [])
        cap = inp.get("caption", "")
        s = ", ".join(files)
        if cap:
            s += "  " + cap
        return shorten(s, 100)
    if name == "ListAgents":
        return "查看当前可用的 agent 列表"
    if name == "SendMessage":
        return "to=%s：%s" % (inp.get("to", ""), inp.get("summary") or shorten(inp.get("message", ""), 50))
    if name == "TaskStop":
        return shorten(json.dumps(inp, ensure_ascii=False))
    if name == "Glob":
        return inp.get("pattern", "")
    if name == "Grep":
        return shorten(inp.get("pattern", ""))
    # 通用兜底
    return shorten(json.dumps(inp, ensure_ascii=False))


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()
    stats["total_lines"] = len(raw_lines)

    seen_uuid = set()
    tool_map = {}  # tool_use_id -> name

    out = []
    current_role = [None]  # 用 list 包一层方便闭包里修改

    def ensure_heading(role):
        if role != current_role[0]:
            out.append("")
            if role == "user":
                out.append("## 🧑 用户")
                stats["user_headings"] += 1
            else:
                out.append("## 🤖 Claude")
                stats["assistant_headings"] += 1
            out.append("")
            current_role[0] = role

    dropped = Counter()

    for idx, line in enumerate(raw_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            dropped["json_error"] += 1
            continue

        t = obj.get("type")
        if t not in ("user", "assistant"):
            dropped["type:" + str(t)] += 1
            continue

        u = obj.get("uuid")
        if u:
            if u in seen_uuid:
                dropped["dup_uuid"] += 1
                continue
            seen_uuid.add(u)

        if obj.get("isSidechain"):
            dropped["sidechain"] += 1
            continue

        if obj.get("isMeta"):
            dropped["isMeta_image_companion"] += 1
            continue

        msg = obj.get("message")
        if not isinstance(msg, dict):
            dropped["no_message"] += 1
            continue
        content = msg.get("content")

        if t == "assistant":
            stats["assistant_lines_raw"] += 1
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "thinking":
                    dropped["thinking_block"] += 1
                    continue
                if btype == "text":
                    text = b.get("text", "")
                    if not text.strip():
                        continue
                    ensure_heading("assistant")
                    out.append(text.strip())
                    out.append("")
                    stats["assistant_text_blocks"] += 1
                elif btype == "tool_use":
                    name = b.get("name", "?")
                    tid = b.get("id")
                    tinput = b.get("input", {})
                    if tid:
                        tool_map[tid] = name
                    ensure_heading("assistant")
                    summ = tool_summary(name, tinput)
                    out.append("> 🔧 %s：%s" % (name, summ))
                    out.append("")
                    stats["tool_calls"] += 1
                    stats["tool:" + name] += 1
                    if name == "Agent":
                        prompt = tinput.get("prompt", "")
                        out.append("<details><summary>派给 subagent 的任务</summary>")
                        out.append("")
                        out.append("```")
                        out.append(prompt)
                        out.append("```")
                        out.append("")
                        out.append("</details>")
                        out.append("")
                else:
                    dropped["assistant_block:" + str(btype)] += 1

        elif t == "user":
            stats["user_lines_raw"] += 1
            if isinstance(content, str):
                origin_kind = (obj.get("origin") or {}).get("kind")
                if origin_kind == "human":
                    text = process_user_text(content)
                    if text.strip():
                        ensure_heading("user")
                        out.append(text.strip())
                        out.append("")
                        stats["user_text_messages"] += 1
                    else:
                        dropped["user_text_emptied_after_strip"] += 1
                elif "task-notification" in content or origin_kind == "task-notification":
                    dropped["task_notification"] += 1
                else:
                    # 其它未知来源的字符串消息，做一次清洗，若还有内容则保留
                    text = process_user_text(content)
                    if text.strip():
                        ensure_heading("user")
                        out.append(text.strip())
                        out.append("")
                        stats["user_text_messages"] += 1
                    else:
                        dropped["user_text_unknown_origin_empty"] += 1
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype == "tool_result":
                        tid = b.get("tool_use_id")
                        name = tool_map.get(tid)
                        is_error = bool(b.get("is_error"))
                        if is_error:
                            if obj.get("toolDenialKind") == "user-rejected":
                                reason = "用户拒绝该操作"
                            else:
                                reason = shorten(get_text(b.get("content")), 60)
                            ensure_heading("assistant")
                            out.append("（工具报错：%s）" % reason)
                            out.append("")
                            stats["tool_errors"] += 1
                        elif name == "Agent":
                            report = get_text(b.get("content"))
                            if report.strip():
                                ensure_heading("assistant")
                                out.append("<details><summary>subagent 返回结果</summary>")
                                out.append("")
                                out.append("```")
                                out.append(report)
                                out.append("```")
                                out.append("")
                                out.append("</details>")
                                out.append("")
                                stats["agent_reports"] += 1
                        else:
                            dropped["tool_result_dropped"] += 1
                    elif btype == "text":
                        text = b.get("text", "")
                        if "[Request interrupted by user" in text:
                            out.append("*%s*" % text.strip())
                            out.append("")
                            stats["interrupted_notes"] += 1
                        else:
                            cleaned = process_user_text(text)
                            if cleaned.strip():
                                ensure_heading("user")
                                out.append(cleaned.strip())
                                out.append("")
                                stats["user_text_messages"] += 1
                            else:
                                dropped["user_list_text_empty"] += 1
                    elif btype == "image":
                        ensure_heading("user")
                        out.append("*[图片]*")
                        out.append("")
                    else:
                        dropped["user_block:" + str(btype)] += 1

    # 组装最终文档
    body = "\n".join(out).strip("\n") + "\n"

    # 路径替换（按顺序，先长后短）
    replacements = [
        ("D:\\新建文件夹\\", ""),
        ("D:\\IMG_2409", "IMG_2409"),
        ("D:\\gen_lihui.py", "gen_lihui.py"),
        ("D:\\edit_lihui_binfa.py", "edit_lihui_binfa.py"),
    ]
    for a, b in replacements:
        body = body.replace(a, b)

    final_text = TITLE + "\n\n" + INTRO + "\n\n" + body

    # 安全检查：sk- 开头的 key，以及邮箱地址
    safety_notes = []
    sk_pattern = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")
    email_pattern = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

    sk_matches = sk_pattern.findall(final_text)
    if sk_matches:
        final_text = sk_pattern.sub("[已隐去]", final_text)
        safety_notes.append("发现并隐去 %d 处疑似 API key（sk- 开头）" % len(sk_matches))

    email_matches = email_pattern.findall(final_text)
    if email_matches:
        final_text = email_pattern.sub("[已隐去]", final_text)
        safety_notes.append("发现并隐去 %d 处邮箱地址：%s" % (len(email_matches), ", ".join(sorted(set(email_matches)))))

    with open(DST, "w", encoding="utf-8") as f:
        f.write(final_text)

    # 输出统计报告到 stdout（用 utf-8 包装避免 gbk 报错）
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=== 输出文件 ===")
    import os
    print(DST, os.path.getsize(DST), "字节")
    print()
    print("=== 统计 ===")
    for k in sorted(stats.keys()):
        print(k, "=", stats[k])
    print()
    print("=== 丢弃的记录类型 ===")
    for k in sorted(dropped.keys()):
        print(k, "=", dropped[k])
    print()
    print("=== 安全检查 ===")
    if safety_notes:
        for n in safety_notes:
            print(n)
    else:
        print("未发现 sk- key 或邮箱地址")


if __name__ == "__main__":
    main()
