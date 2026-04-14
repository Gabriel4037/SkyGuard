  // Backend endpoints (you must implement these on server for 4.1/4.2)
  // =======================
  const DETECT_ENDPOINT = '/api/drone/detect';
  const CLIP_SAVE_ENDPOINT = '/api/clip/save';

  // Stored logs APIs:
  const LOGS_LIST_ENDPOINT   = '/api/logs';           // GET -> [{id,time,event,source,clip}]
  const LOGS_CREATE_ENDPOINT = '/api/logs/create';    // POST -> {ok:true,id}
  const LOGS_UPDATE_ENDPOINT = '/api/logs/update';    // POST -> {ok:true}
  const LOGS_DELETE_ENDPOINT = '/api/logs/delete';    // POST -> {ok:true}
  const CLIP_DOWNLOAD_PREFIX = '/api/clip/download?file='; // GET download by filename

  // =======================
  // i18n
  // =======================
  const I18N = {
    en: {
      status: "Status",
      dashboard: "Dashboard",
      live: "Live",
      videoFile: "Video",
      logs: "Logs",
      settings: "Settings",
      admin: "Admin",

      mainMenu: "Main Menu",
      addCamera: "Add Camera",
      refreshDevices: "Refresh Devices",
      startAll: "Start All",
      stopAll: "Stop All",
      detectOnAll: "Detect On (All)",
      detectOffAll: "Detect Off (All)",
      dashHint: "Live previews of attached cameras are shown below. Start cameras first, then enable detection.",

      recentLogs: "Recent Logs",
      lastDetect: "Last detect",
      clearLogs: "Clear",
      logsPageHint: "Open the Logs page to manage stored logs and download specific footage.",
      time: "Time",
      event: "Event",
      source: "Source",
      clipSaved: "Clip Saved",

      liveMonitor: "Live Monitor",
      liveHint: "Same camera tiles as Dashboard. If you prefer, just use Dashboard.",

      videoFileDetection: "Video File Detection",
      startDetect: "Start Detect",
      stopDetect: "Stop Detect",
      loadVideo: "Load Video File",

      storedLogs: "Stored Logs",
      storedLogsHint: "This page loads logs saved on this detector node. Use the central server admin app to manage uploaded logs and users.",
      reload: "Reload",
      adminLogin: "Admin Login",
      adminLogout: "Admin Logout",
      clip: "Clip",
      actions: "Actions",
      download: "Download",
      edit: "Edit",

      detectionFps: "Detection Rate (FPS)",
      confidence: "Detection Confidence",
      maxDim: "Detection Image Size",
      modelCheckInterval: "Model Check Interval (seconds)",
      currentModel: "Current Model",
      pendingModel: "Pending Model",
      lastModelCheck: "Last Model Check",
      lastSync: "Last Sync",
      autoSaveClip: "Save a clip automatically when a drone is detected",
      clipSaveMode: "Clip Save Mode",
      clipModeEvent: "Save for the full detection event",
      clipModeFixed: "Save a fixed clip length",
      clipSec: "Clip Length (seconds)",
      bgColor: "Background color",
      language: "Language",
      clipBurnInHint: "Saved clips are recorded from the annotated canvas, so the footage includes boxes, trail, prediction curve, and timestamp.",
      eventModeCap: "Event-based recording stops after the drone disappears, with a short delay. Safety cap: 2 minutes.",
      close: "Close",

      adminPassword: "Admin password",
      login: "Login",
      adminHint: "This is frontend-only gating. You should still enforce admin permission on the server.",

      editLog: "Edit Log",
      save: "Save",
      delete: "Delete",

      alertTitle: "Drone Detected",
      alertBody: "Detection triggered on",
      syncNow: "Sync Now",
      updateModel: "Update Model"
    },

    "zh-Hant": {
      status: "狀態",
      dashboard: "主頁",
      live: "即時",
      videoFile: "影片",
      logs: "紀錄",
      settings: "設定",
      admin: "管理員",

      mainMenu: "主選單",
      addCamera: "新增鏡頭",
      refreshDevices: "重新掃描鏡頭",
      startAll: "全部啟動",
      stopAll: "全部停止",
      detectOnAll: "全部偵測開啟",
      detectOffAll: "全部偵測關閉",
      dashHint: "下方會顯示已連接鏡頭的即時預覽。先啟動鏡頭，再開啟偵測。",

      recentLogs: "近期紀錄",
      lastDetect: "偵測耗時",
      clearLogs: "清除",
      logsPageHint: "到「紀錄」頁面可管理已存紀錄並下載對應影片。",
      time: "時間",
      event: "事件",
      source: "來源",
      clipSaved: "已存影片",

      liveMonitor: "即時監控",
      liveHint: "與主頁相同的鏡頭方塊。你也可以只用主頁。",

      videoFileDetection: "影片檔偵測",
      startDetect: "開始偵測",
      stopDetect: "停止偵測",
      loadVideo: "載入影片檔",

      storedLogs: "已存紀錄",
      storedLogsHint: "此頁會載入此偵測端本機的紀錄。全域上傳紀錄與使用者管理請在中央伺服器管理端進行。",
      reload: "重新載入",
      adminLogin: "管理員登入",
      adminLogout: "管理員登出",
      clip: "影片",
      actions: "操作",
      download: "下載",
      edit: "編輯",

      detectionFps: "偵測 FPS",
      confidence: "信心值",
      maxDim: "最大尺寸(後端)",
      modelCheckInterval: "模型檢查間隔(秒)",
      currentModel: "目前模型",
      pendingModel: "待套用模型",
      lastModelCheck: "上次檢查模型",
      lastSync: "上次同步",
      autoSaveClip: "偵測到無人機自動存檔",
      clipSaveMode: "存檔模式",
      clipModeEvent: "無人機出現期間存檔(直到消失)",
      clipModeFixed: "固定秒數存檔(依 Clip sec)",
      clipSec: "Clip sec(固定模式)",
      bgColor: "背景顏色",
      language: "語言",
      clipBurnInHint: "影片由標註後畫面錄製，因此包含框、尾跡、曲線預測與時間戳。",
      eventModeCap: "「出現期間存檔」會在目標消失(短延遲)後結束。安全上限：2 分鐘。",
      close: "關閉",

      adminPassword: "管理員密碼",
      login: "登入",
      adminHint: "此為前端門檻，後端仍需做管理權限驗證。",

      editLog: "編輯紀錄",
      save: "儲存",
      delete: "刪除",

      alertTitle: "偵測到無人機",
      alertBody: "偵測來源：",
      syncNow: "立即同步",
      updateModel: "更新模型"
    },

    "zh-Hans": {
      status: "状态",
      dashboard: "主页",
      live: "实时",
      videoFile: "视频",
      logs: "记录",
      settings: "设置",
      admin: "管理员",

      mainMenu: "主菜单",
      addCamera: "添加摄像头",
      refreshDevices: "刷新设备",
      startAll: "全部启动",
      stopAll: "全部停止",
      detectOnAll: "全部开启检测",
      detectOffAll: "全部关闭检测",
      dashHint: "下方显示已连接摄像头的实时预览。先启动摄像头，再开启检测。",

      recentLogs: "近期记录",
      lastDetect: "检测耗时",
      clearLogs: "清除",
      logsPageHint: "到「记录」页面可管理已存记录并下载对应视频。",
      time: "时间",
      event: "事件",
      source: "来源",
      clipSaved: "已存视频",

      liveMonitor: "实时监控",
      liveHint: "与主页相同的摄像头卡片。你也可以只用主页。",

      videoFileDetection: "视频文件检测",
      startDetect: "开始检测",
      stopDetect: "停止检测",
      loadVideo: "加载视频文件",

      storedLogs: "已存记录",
      storedLogsHint: "此页加载的是当前检测节点本地记录。全局上传记录和用户管理请在中央服务器管理端进行。",
      reload: "重新加载",
      adminLogin: "管理员登录",
      adminLogout: "管理员登出",
      clip: "视频",
      actions: "操作",
      download: "下载",
      edit: "编辑",

      detectionFps: "检测 FPS",
      confidence: "置信度",
      maxDim: "最大尺寸(后端)",
      modelCheckInterval: "模型检查间隔(秒)",
      currentModel: "当前模型",
      pendingModel: "待应用模型",
      lastModelCheck: "上次检查模型",
      lastSync: "上次同步",
      autoSaveClip: "检测到无人机自动保存",
      clipSaveMode: "保存模式",
      clipModeEvent: "无人机出现期间保存(直到消失)",
      clipModeFixed: "固定秒数保存(按 Clip sec)",
      clipSec: "Clip sec(固定模式)",
      bgColor: "背景颜色",
      language: "语言",
      clipBurnInHint: "视频从标注画面录制，因此包含框、尾迹、曲线预测与时间戳。",
      eventModeCap: "「出现期间保存」会在目标消失(短延迟)后结束。安全上限：2 分钟。",
      close: "关闭",

      adminPassword: "管理员密码",
      login: "登录",
      adminHint: "这是前端门槛，后端仍需做管理员权限校验。",

      editLog: "编辑记录",
      save: "保存",
      delete: "删除",

      alertTitle: "检测到无人机",
      alertBody: "检测来源：",
      syncNow: "立即同步",
      updateModel: "更新模型"
    }
  };

  function getLang() {
    return localStorage.getItem('lang') || 'en';
  }
  function setLang(lang) {
    localStorage.setItem('lang', lang);
    applyI18n();
  }
  function t(key) {
    const lang = getLang();
    return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
  }
  function applyI18n() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      if (!key) return;
      el.textContent = t(key);
    });
  }

