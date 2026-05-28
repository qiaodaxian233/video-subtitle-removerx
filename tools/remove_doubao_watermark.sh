#!/bin/bash
# 豆包水印批量去除脚本 v3 (Linux/Mac/WSL)
# 用法: ./remove_doubao_watermark.sh <输入文件夹> [输出文件夹] [模式]
#   模式: propainter (默认，质量最好) | sttn-auto (快但有时残影)
# 默认坐标针对 1280x720 横屏豆包视频。不同视频水印宽度不同，先用 measure_doubao_watermark_box.py 自测。

INPUT_DIR="${1:-./input}"
OUTPUT_DIR="${2:-./output}"
MODE="${3:-propainter}"

# 豆包水印坐标 1280x720 (针对"豆包AI生成"5字水印实测)
# 格式: YMIN YMAX XMIN XMAX
TL_COORDS="0 100 0 260"         # 左上 (100x260 px)
BR_COORDS="620 700 1030 1280"   # 右下 (80x250 px)

mkdir -p "$OUTPUT_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# 自动激活项目自带的 videoEnv (如果存在)
if [ -d "videoEnv" ]; then
    if [ -f "videoEnv/bin/activate" ]; then
        source videoEnv/bin/activate
    elif [ -f "videoEnv/Scripts/activate" ]; then
        source videoEnv/Scripts/activate
    fi
fi

total=$(ls "$INPUT_DIR"/*.mp4 2>/dev/null | wc -l)
if [ "$total" -eq 0 ]; then
    echo "❌ $INPUT_DIR 里没有 mp4 文件"
    exit 1
fi

echo "📁 找到 $total 个视频文件"
echo "🔧 模式: $MODE"
echo "📍 水印坐标: TL=$TL_COORDS  BR=$BR_COORDS"
echo ""

i=0
for video in "$INPUT_DIR"/*.mp4; do
    i=$((i + 1))
    filename=$(basename "$video")
    output="$OUTPUT_DIR/${filename%.mp4}_clean.mp4"

    if [ -f "$output" ]; then
        echo "[$i/$total] ⏭️  跳过已处理: $filename"
        continue
    fi

    echo "[$i/$total] 🎬 处理中: $filename"
    start=$(date +%s)

    python backend/main.py \
        -i "$video" \
        -o "$output" \
        -c $TL_COORDS \
        -c $BR_COORDS \
        --inpaint-mode "$MODE"

    if [ $? -eq 0 ]; then
        elapsed=$(($(date +%s) - start))
        echo "[$i/$total] ✅ 完成，用时 ${elapsed}s → $output"
    else
        echo "[$i/$total] ❌ 失败: $filename"
    fi
    echo ""
done

echo "🎉 全部完成"
