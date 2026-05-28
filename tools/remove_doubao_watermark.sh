#!/bin/bash
# 豆包水印批量去除脚本 v2 (修正坐标)
# 用法: ./remove_doubao_watermark.sh /输入文件夹 /输出文件夹
# 默认坐标针对 1280x720 横屏。9:16 竖屏短剧需重新测坐标。

INPUT_DIR="${1:-./input}"
OUTPUT_DIR="${2:-./output}"

# 豆包水印坐标 (1280x720 横屏, 已实测可去除)
# 格式: YMIN YMAX XMIN XMAX
TL_COORDS="5 105 30 290"        # 左上 (100x260 px)
BR_COORDS="605 720 980 1240"    # 右下 (115x260 px)

mkdir -p "$OUTPUT_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

total=$(ls "$INPUT_DIR"/*.mp4 2>/dev/null | wc -l)
if [ "$total" -eq 0 ]; then
    echo "❌ $INPUT_DIR 里没有 mp4 文件"
    exit 1
fi

echo "📁 找到 $total 个视频文件"
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
        --inpaint-mode sttn-auto

    if [ $? -eq 0 ]; then
        elapsed=$(($(date +%s) - start))
        echo "[$i/$total] ✅ 完成，用时 ${elapsed}s → $output"
    else
        echo "[$i/$total] ❌ 失败: $filename"
    fi
    echo ""
done

echo "🎉 全部完成"
