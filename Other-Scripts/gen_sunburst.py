"""用 GPT Image 2.5 Sunburst 以两张参考图 + 提示词生成新图。

用法：在下面 PROMPT 里填提示词，然后运行
    D:\\Programs\\DevEnvironments\\Anaconda\\anaconda3\\envs\\myenv\\python.exe gen_sunburst.py
也可以命令行直接传提示词：python gen_sunburst.py "你的提示词"
"""
import base64
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

# ======== 在这里填提示词 ========
PROMPT = """画一张Claude娘和ChatGPT娘接吻的横版图片，我（图1）在远处惊讶地看着，瞪着大白眼、张大嘴巴，一脸震惊
背景空白
风格：干净的赛璐璐上色，线条清晰。整体要有梗图的喜感和夸张感。不要出现文字。请绘制得卡通一点
Claude娘和ChatGPT娘为主角，我只占比较小一部分"""
# ================================

ENV_FILE = Path(r"D:\Release\WickMind\config\.env")
IMAGES = [
    Path(r"D:\Kandao\Pictures\[ 相册 ]\立绘.png"),
    Path(r"D:\Kandao\Pictures\[ 相册 ]\7·其他\x.com HTTR5b_XMAARuU0.jpg"),
]
OUT_DIR = Path(__file__).resolve().parent / "output"
MODEL = "gpt-image-2.5-sunburst"


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else PROMPT
    if not prompt.strip():
        sys.exit("提示词为空：请在脚本里的 PROMPT 填写，或命令行传入。")

    env = load_env(ENV_FILE)
    client = OpenAI(
        api_key=env["OPENAI_API_KEY"],
        base_url=env.get("OPENAI_BASE_URL") or None,
        timeout=600,
    )

    files = [open(p, "rb") for p in IMAGES]
    try:
        print(f"正在生成（{MODEL}，size/quality=auto）……")
        t0 = time.time()
        # 注意：该模型不支持 input_fidelity 参数，不要加
        resp = client.images.edit(
            model=MODEL,
            image=files,
            prompt=prompt,
            size="auto",
            quality="auto",
        )
    finally:
        for f in files:
            f.close()

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, item in enumerate(resp.data):
        out = OUT_DIR / f"sunburst_{stamp}_{i}.png"
        out.write_bytes(base64.b64decode(item.b64_json))
        print(f"已保存：{out}")
    print(f"耗时 {time.time() - t0:.0f} 秒")
    if getattr(resp, "usage", None):
        print(f"用量：{resp.usage}")


if __name__ == "__main__":
    main()
