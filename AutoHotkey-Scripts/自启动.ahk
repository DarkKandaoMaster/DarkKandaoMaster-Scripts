;启动器脚本：由计划任务“登录时自启动AHK脚本”在用户登录后（延迟10秒）调用，依次启动下面列表里的各个.ahk脚本，启动完自己就退出，不常驻
;增删自启脚本：直接编辑下面的scripts数组即可，元素可以是绝对路径（带盘符如 D:\...，或以 \\ 开头的网络路径），也可以是相对于本脚本所在目录的相对路径；不想自启的脚本在行首加分号注释掉
;注意：列表里写的是相对路径（与本脚本同目录），仓库目录整体移动后无需改这里；但计划任务里本脚本的路径需要同步修改
;计划任务名称：登录时自启动AHK脚本（任务计划程序库根目录 \ 下），运行的是 AutoHotkey64.exe "本脚本完整路径"
#Requires AutoHotkey v2.0 ;我的AutoHotkey是2.0版本的，v2和v1语法不同，所以写上这么一句，如果使用v1解释器运行该脚本那么会报错

scripts := [ ;需要自启动的脚本列表：可写绝对路径，也可写相对于A_ScriptDir的相对路径
    "Alt+Tab只在最近两个窗口切换.ahk",
    "PrtSc映射为Win+PrtSc.ahk",
    "ImageGlass 按1待收藏、按2待处理.ahk",
    "mpvnet 长按右方向键三倍速播放.ahk"
]

ResolvePath(name){ ;把列表元素解析成完整路径：第2个字符是冒号（带盘符）或以\\开头（网络路径）的视为绝对路径原样返回，否则当作相对路径拼到本脚本目录后面
    if (SubStr(name, 2, 1) = ":" || SubStr(name, 1, 2) = "\\")
        return name
    return A_ScriptDir "\" name
}

missing := [] ;收集找不到的脚本（完整路径）
for name in scripts { ;遍历列表，先解析成完整路径；存在就用当前解释器（A_AhkPath）启动，不存在就记下来
    fullPath := ResolvePath(name)
    if FileExist(fullPath)
        Run('"' A_AhkPath '" "' fullPath '"')
    else
        missing.Push(fullPath)
}

if missing.Length { ;有缺失的脚本时弹一个提示框列出来
    list := ""
    for p in missing
        list .= p "`n"
    MsgBox("以下脚本不存在，未能启动：`n`n" list, "自启动.ahk")
}

ExitApp ;启动器跑完就退出