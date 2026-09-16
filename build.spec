# -*- mode: python ; coding: utf-8 -*-
# H3C 云桌面智能运维助手 — PyInstaller 打包配置（单文件）
# 等价命令：
#   pyinstaller --onefile --windowed --name H3COpsAssistant main.py
# 打包后数据目录由应用按版本号定位（C:\Users\liwanghao\Desktop\AI工具文件\v1.0.0），
# 与 exe 自身路径解耦，故无需 --add-data 绑定运行时数据目录。

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],                      # 配置/知识库/日志均落在固定数据目录，不进包
    hiddenimports=[
        'core', 'core.paths', 'core.config', 'core.logging_setup',
        'core.digest_auth', 'core.workspace_client', 'core.models', 'core.errors',
        # 平台前端会话登录（DES/ECB/Pkcs7 密码加密），依赖 pycryptodome
        'core.frontend_auth', 'Crypto', 'Crypto.Cipher', 'Crypto.Cipher.DES',
        'Crypto.Random', 'Crypto.Util', 'Crypto.Util.Padding', 'Crypto.Hash',
        'ai', 'ai.llm_client', 'ai.retriever_base', 'ai.local_retriever',
        'ai.ima_retriever', 'ai.knowledge_manager', 'ai.analyzer', 'ai.web_search',
        'update', 'update.version_manager',
        'workers', 'workers.base_worker', 'workers.alarm_worker',
        'workers.health_worker', 'workers.analyze_worker', 'workers.version_worker',
        'ui', 'ui.main_window', 'ui.styles', 'ui.widgets', 'ui.config_dialog',
        'ui.alarm_panel', 'ui.health_panel', 'ui.knowledge_panel', 'ui.update_panel',
        'ui.error_messages', 'ui.analysis_dialog', 'ui.alarm_filters', 'ui.table_utils',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='H3COpsAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                # --windowed 等价：无控制台窗口
    windowed=True,
)
