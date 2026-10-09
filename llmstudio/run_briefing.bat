@echo off
rem 每日早报定时任务入口
cd /d d:\llmstudio
echo ====== %date% %time% ====== >> briefing_log.txt
python generate_briefing.py >> briefing_log.txt 2>&1