"""Upload the committed project to a Hugging Face Space (Docker SDK). The Space builds the Dockerfile.

Only committed files go up (git archive of HEAD), so .env, .venv and local files never leave this
laptop. The Space gets its own README card (deploy/space_README.md) instead of the GitHub README.
Uploading through huggingface_hub (not git push) also handles the binary files (images) for us.

Once:   .venv\\Scripts\\python -m pip install huggingface_hub
        .venv\\Scripts\\hf auth login        (paste a Hugging Face token with "write" access)
Deploy: .venv\\Scripts\\python deploy\\push_space.py <hf-username>/<space-name>
"""
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]

if len(sys.argv) != 2 or "/" not in sys.argv[1]:
    sys.exit("usage: python deploy/push_space.py <hf-username>/<space-name>")
space = sys.argv[1]

if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip():
    print("Note: there are uncommitted changes; only the last commit is uploaded.")
commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

# The committed files, with Linux line endings (core.autocrlf would turn them into Windows ones).
archive = subprocess.run(["git", "-c", "core.autocrlf=false", "archive", "--format=tar", "HEAD"],
                         cwd=ROOT, capture_output=True, check=True).stdout

api = HfApi()
try:
    print("Signed in to Hugging Face as:", api.whoami()["name"])
except Exception:
    sys.exit("Not signed in to Hugging Face. Run: .venv\\Scripts\\hf auth login  (token with write access)")
api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)

with tempfile.TemporaryDirectory() as tmp:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp, filter="data")
    (Path(tmp) / "README.md").write_text((ROOT / "deploy" / "space_README.md").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    api.upload_folder(folder_path=tmp, repo_id=space, repo_type="space",
                      commit_message=f"Deploy {commit} from GitHub")

print(f"Uploaded commit {commit}. The Space is building now (about 5 minutes):")
print(f"  page and build log: https://huggingface.co/spaces/{space}")
print(f"  the app itself:     https://{space.replace('/', '-').replace('_', '-').lower()}.hf.space")
