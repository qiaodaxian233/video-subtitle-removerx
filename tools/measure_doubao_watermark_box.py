#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
豆包水印坐标测量工具

策略 (实用主义):
  1. 抽取覆盖全片的多帧
  2. 对每帧做"高亮像素 mask"
  3. 累加所有帧的 mask → 热力图
  4. 输出热力图 + 网格坐标参考图，让你肉眼看哪里亮 → 直接读坐标
  5. 同时自动给出"扫描每个角落"的建议 box (不一定全对，但是起点)

为什么不全自动:
  - 豆包水印会跳角，单角落只在 ~30-50% 时间出现
  - 不同视频的水印宽度 ("豆包AI" 3字 vs "豆包AI生成" 5字) 不同
  - 视频内容里可能有亮元素干扰检测
  → 自动算法不稳，热力图人眼判断最快最准

用法:
  python tools/measure_doubao_watermark_box.py 视频.mp4
  打开输出的 _heatmap.png 看哪里有红色亮块 → 那就是水印。
  对照图上的网格读出 YMIN YMAX XMIN XMAX。
"""
import argparse
import os
import sys
import cv2
import numpy as np


def extract_frames(video_path, n=30):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        sys.exit(f"无法打开视频: {video_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if total < n:
        n = max(2, total // 2)
    idxs = np.linspace(0, total - 1, n).astype(int)
    gray_frames = []
    color_frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            color_frames.append(f.copy())
            gray_frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    cap.release()
    return gray_frames, color_frames, W, H, fps


def watermark_score_map(gray_frames):
    """每像素打分: 在多少比例的帧中被判为"高亮"."""
    stack = np.stack(gray_frames).astype(np.float32)  # (N, H, W)
    per_frame_mean = stack.mean(axis=(1, 2), keepdims=True)
    per_frame_std = stack.std(axis=(1, 2), keepdims=True)
    threshold = per_frame_mean + 1.0 * per_frame_std  # 更严, 避免普通亮区干扰
    high = (stack > threshold).astype(np.float32)
    return high.mean(axis=0)  # (H, W) ∈ [0, 1]


def suggest_corner_boxes(score, H, W, margin=10):
    """
    扫描四角，给出建议 box (不保证全对，只作参考)。
    阈值用"该角落区域 score 的 top 5% 像素"自适应。
    """
    suggestions = {}
    # 角落初始扫描区: 1/4 宽 × 1/6 高 (覆盖典型豆包水印大小, 留余量)
    h_init = max(80, H // 6)
    w_init = max(220, W // 4)

    corners = {
        'TL': (0, h_init, 0, w_init),
        'TR': (0, h_init, W - w_init, W),
        'BL': (H - h_init, H, 0, w_init),
        'BR': (H - h_init, H, W - w_init, W),
    }

    for name, (y0, y1, x0, x1) in corners.items():
        roi = score[y0:y1, x0:x1]
        # 用区域内 score top 10% 像素的下界做阈值, 这样自适应水印出现频率
        if roi.max() < 0.15:  # 整个角落都没什么亮像素 → 真没水印
            continue
        thresh = max(0.15, np.percentile(roi, 90))
        ys, xs = np.where(roi > thresh)
        if len(ys) < 30:
            continue
        ty0 = y0 + int(ys.min()) - margin
        ty1 = y0 + int(ys.max()) + 1 + margin
        tx0 = x0 + int(xs.min()) - margin
        tx1 = x0 + int(xs.max()) + 1 + margin
        # 推到画面边缘
        if name in ('TL', 'TR'):
            ty0 = 0
        if name in ('BL', 'BR'):
            ty1 = H
        if name in ('TL', 'BL'):
            tx0 = 0
        if name in ('TR', 'BR'):
            tx1 = W
        ty0 = max(0, ty0); tx0 = max(0, tx0)
        ty1 = min(H, ty1); tx1 = min(W, tx1)
        suggestions[name] = (ty0, ty1, tx0, tx1)
    return suggestions


def render_heatmap(score, base_frame, suggestions, out_path):
    """渲染热力图 + 网格 + 建议 box, 保存给用户肉眼判断."""
    H, W = score.shape
    # 热力图 (score 越高越红)
    heat = (np.clip(score, 0, 1) * 255).astype(np.uint8)
    heat_color = cv2.applyColorMap(heat, cv2.COLORMAP_JET)
    # 叠加到原始帧
    vis = cv2.addWeighted(base_frame, 0.45, heat_color, 0.55, 0)

    # 画网格 (每 100px 一条灰线)
    for x in range(100, W, 100):
        cv2.line(vis, (x, 0), (x, H), (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(vis, str(x), (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, (255, 255, 255), 1, cv2.LINE_AA)
    for y in range(100, H, 100):
        cv2.line(vis, (0, y), (W, y), (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(vis, str(y), (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, (255, 255, 255), 1, cv2.LINE_AA)

    # 画建议 box (绿色, 粗)
    for name, (y0, y1, x0, x1) in suggestions.items():
        cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 255, 0), 3)
        cv2.putText(vis, name, (x0 + 5, y0 + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    cv2.imwrite(out_path, vis)


def main():
    ap = argparse.ArgumentParser(description="豆包视频水印坐标测量工具")
    ap.add_argument("video", help="视频文件路径")
    ap.add_argument("--frames", type=int, default=30, help="抽多少帧分析 (默认 30)")
    ap.add_argument("--margin", type=int, default=10,
                    help="自动建议的 box 外扩 padding (默认 10)")
    ap.add_argument("-o", "--output", default=None,
                    help="热力图输出路径 (默认: 视频同目录 <视频名>_heatmap.png)")
    args = ap.parse_args()

    if not os.path.isfile(args.video):
        sys.exit(f"文件不存在: {args.video}")

    print(f"📹 分析: {args.video}")
    gray_frames, color_frames, W, H, fps = extract_frames(args.video, n=args.frames)
    if len(gray_frames) < 2:
        sys.exit("帧太少，无法分析")
    print(f"   分辨率 {W}x{H}, FPS {fps:.1f}, 取 {len(gray_frames)} 帧")

    score = watermark_score_map(gray_frames)

    # 用第一帧做底，叠热力图
    base = color_frames[0]
    suggestions = suggest_corner_boxes(score, H, W, margin=args.margin)

    # 输出热力图
    if args.output is None:
        d = os.path.dirname(os.path.abspath(args.video)) or "."
        b = os.path.splitext(os.path.basename(args.video))[0]
        out_path = os.path.join(d, f"{b}_heatmap.png")
    else:
        out_path = args.output
    render_heatmap(score, base, suggestions, out_path)
    print(f"\n🖼️  热力图已保存: {out_path}")
    print(f"   红色/黄色 = 水印高发区, 蓝色 = 背景")

    if suggestions:
        print(f"\n💡 自动建议 box ({len(suggestions)} 个, 仅供参考):\n")
        cli_args = []
        for corner, (y0, y1, x0, x1) in suggestions.items():
            h, w = y1 - y0, x1 - x0
            print(f"  {corner}: YMIN={y0} YMAX={y1} XMIN={x0} XMAX={x1}  ({h}x{w} px)")
            cli_args.append(f"-c {y0} {y1} {x0} {x1}")
        print(f"\n📋 一键复制 (先看热力图确认这些 box 真的盖住水印):\n")
        print(f"  {' '.join(cli_args)}")
        print(f"\n🔧 完整命令:\n")
        print(f"  python backend/main.py -i \"输入.mp4\" -o \"输出.mp4\" \\")
        print(f"    {' '.join(cli_args)} --inpaint-mode propainter")
    else:
        print("\n⚠️  没找到明显的水印区域，请打开热力图自己判断。")

    print(f"\n📐 使用方法:")
    print(f"  1. 打开 {os.path.basename(out_path)}")
    print(f"  2. 找红色/黄色亮块 — 那就是水印位置")
    print(f"  3. 对照图上网格读出 YMIN YMAX XMIN XMAX (各 ±10px 留余量)")
    print(f"  4. 替换到批处理脚本 / 命令里")


if __name__ == "__main__":
    main()
