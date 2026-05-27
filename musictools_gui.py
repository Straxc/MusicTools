#!/usr/bin/env python3
"""MusicTools GUI 版 — 直接启动图形界面，无命令行菜单"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from musictools import run_gui
run_gui()
