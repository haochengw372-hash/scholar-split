"""Configure the local DeepSeek key without putting it in command arguments."""
import getpass
import os
from pathlib import Path
import toml
from utils.config_migration import prepare_config_files

root = Path(__file__).resolve().parent
prepare_config_files({
    "pdf2zh": str(root / "config/config.json"),
    "pdf2zh_next": str(root / "config/config.toml"),
    "venv": str(root / "config/venv.json"),
})
path = root / "config/config.toml"
config = toml.load(path)
detail = config.setdefault("deepseek_detail", {})
model = input(f"DeepSeek 模型 [{detail.get('deepseek_model', 'deepseek-v4-flash')}]：").strip()
key = getpass.getpass("DeepSeek API Key（输入不显示，留空保留现有配置）：").strip()
if model:
    detail["deepseek_model"] = model
if key:
    detail["deepseek_api_key"] = key
config["deepseek"] = True
config["siliconflowfree"] = False
with path.open("w", encoding="utf-8") as handle:
    toml.dump(config, handle)
os.chmod(path, 0o600)
print("已保存本机配置；请在 Zotero 的翻译服务中选择 DeepSeek。")
