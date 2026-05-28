@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ==============================================================
REM 豆包 AI 视频水印批量去除 - Windows
REM 用法: remove_doubao_watermark.bat ^<输入文件夹^> [输出文件夹] [模式]
REM   模式: propainter (默认，质量最好) ^| sttn-auto (快但有时残影)
REM ==============================================================
REM 默认坐标针对 1280x720 横屏豆包视频("豆包AI生成"5字水印)。
REM 不同豆包视频水印宽度不同。如果残留:
REM   python tools\measure_doubao_watermark_box.py 你的视频.mp4
REM 它会输出本视频的精确坐标，替换下面的 TL_COORDS / BR_COORDS。
REM ==============================================================

set "INPUT_DIR=%~1"
set "OUTPUT_DIR=%~2"
set "MODE=%~3"

if "%INPUT_DIR%"=="" (
    echo [错误] 用法: %~nx0 ^<输入文件夹^> [输出文件夹] [propainter^|sttn-auto]
    exit /b 1
)
if "%OUTPUT_DIR%"=="" set "OUTPUT_DIR=.\output"
if "%MODE%"=="" set "MODE=propainter"

REM 豆包水印坐标 (1280x720 横屏，已实测)
REM 格式: YMIN YMAX XMIN XMAX
set "TL_COORDS=0 100 0 260"
set "BR_COORDS=620 700 1030 1280"

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

REM 切到项目根 (脚本在 tools\ 下)
pushd "%~dp0.."

REM 激活项目自带的 videoEnv (跟 start.bat 一致)
if not exist "videoEnv\Scripts\activate.bat" (
    echo [错误] 找不到 videoEnv 虚拟环境
    echo 请确认你在项目根目录下有 videoEnv 文件夹
    popd & exit /b 1
)
call videoEnv\Scripts\activate.bat

REM 统计文件数
set /a total=0
for %%f in ("%INPUT_DIR%\*.mp4") do set /a total+=1
if %total%==0 (
    echo [错误] %INPUT_DIR% 里没有 mp4 文件
    popd & exit /b 1
)

echo.
echo [INFO] 找到 %total% 个视频
echo [INFO] 模式: %MODE%
echo [INFO] 坐标: TL=%TL_COORDS%  BR=%BR_COORDS%
echo.

set /a i=0
for %%f in ("%INPUT_DIR%\*.mp4") do (
    set /a i+=1
    set "filename=%%~nxf"
    set "outpath=%OUTPUT_DIR%\%%~nf_clean.mp4"

    if exist "!outpath!" (
        echo [!i!/%total%] [SKIP] 已存在: !filename!
    ) else (
        echo [!i!/%total%] [RUN ] !filename!
        python backend\main.py -i "%%f" -o "!outpath!" -c %TL_COORDS% -c %BR_COORDS% --inpaint-mode %MODE%
        if errorlevel 1 (
            echo [!i!/%total%] [FAIL] !filename!
        ) else (
            echo [!i!/%total%] [DONE] -^> !outpath!
        )
    )
    echo.
)

echo [INFO] 全部完成
popd
endlocal
