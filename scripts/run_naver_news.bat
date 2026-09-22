@echo off
rem 네이버 종목뉴스 수집기 실행 (Windows 작업 스케줄러용). 이 파일은 scripts\ 안에 있으므로 저장소 루트로 이동한다.
cd /d %~dp0..
if not exist data\naver_news mkdir data\naver_news
python scripts\naver_news_collector.py >> data\naver_news\run_bat.log 2>&1
