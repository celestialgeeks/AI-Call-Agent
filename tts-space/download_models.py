"""Pre-download Kokoro FP16 ONNX weights, tokenizer, voices at Docker build time."""
from huggingface_hub import hf_hub_download

REPO = "onnx-community/Kokoro-82M-v1.0-ONNX"
FILES = ["onnx/model_fp16.onnx", "tokenizer.json"]
VOICES = ["af_heart.bin", "af_bella.bin", "am_michael.bin"]

if __name__ == "__main__":
    for f in FILES:
        print("model file:", hf_hub_download(REPO, f))
    for v in VOICES:
        print("voice:", hf_hub_download(REPO, f"voices/{v}"))
