# 智能垃圾分拣上位机

本项目用 OpenCV 读取视频或摄像头画面，用 YOLO 权重 `Predict/best.pt` 识别目标并映射到四类垃圾。分拣控制器根据连续帧判定是否发送指令，收到下位机的 `ACTION_DONE` 后才更新计数。普通电脑可以用 Docker 和 TCP 模拟下位机完成演示。通信帧定义见 [PROTOCOL.md](PROTOCOL.md)。

## Docker 启动

在装有 Docker Engine 和 Compose 的 Linux 电脑上，或开启了 **WSL 2 后端并使用 Linux 容器**的 Windows Docker Desktop 上，从项目根目录运行：

```bash
docker compose up --build -d
docker compose ps
```

浏览器打开 [noVNC 操作界面](http://localhost:6080/vnc.html?autoconnect=1&resize=scale)。程序启动后会准备默认视频 `assets/assessment_demo.mp4`，并连接容器网络内的模拟下位机 `lower-sim:9000`。此时可以先「导入视频」或「打开摄像头」，然后点击「开始识别」。也可以直接点击「开始识别」播放默认视频。默认视频模式无需摄像头或实体串口。修改源码后用 `--build` 重新构建。如果只是再次启动已构建的版本，可以用 `docker compose up -d`。

```bash
docker compose logs --tail 100 app lower-sim
docker compose down
```

## 在 Docker 中使用摄像头

需要先让 Docker 所在的 Linux 环境识别摄像头，出现 `/dev/videoN`，再用 Compose 将该设备映射进 `app` 容器。noVNC 只负责显示容器里的界面，浏览器的摄像头权限不会把设备交给容器。

### Windows：Docker Desktop + WSL 2

1. 启动 Docker Desktop，选择 WSL 2 后端和 Linux 容器，关闭 Windows 上正在使用摄像头的应用。在项目根目录的 PowerShell 运行 [setup-camera.ps1](setup-camera.ps1)。

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-camera.ps1
   ```

2. 脚本检查 `usbipd`，缺少时尝试通过 WinGet 安装 [usbipd-win](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)。随后按需共享摄像头、挂接到 `docker-desktop` 的 WSL 2 环境、加载 `uvcvideo`，并等待采集节点出现。首次共享时，如果 Windows 弹出管理员权限提示，点「是」。

3. 如果脚本无法唯一确定摄像头，先用 `usbipd list` 查询 BUSID，再用 `-BusId` 指定设备。下面以本项目演示用的 `4-2` 为例。如果脚本列出了多个采集节点，可再用 `-VideoDevice /dev/video2` 指定其中一个，路径以脚本输出为准。

   ```powershell
   usbipd list
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-camera.ps1 -BusId 4-2
   ```

看到 `Configured ... in compose.camera.yaml` 后，摄像头映射已经写入并通过 Compose 校验。脚本到这里结束，镜像构建和容器启动见下方「手动构建和启动」。

#### 复位摄像头

用完摄像头或准备重新演示配置时，先停止带设备映射的容器，再运行 [reset-camera.ps1](reset-camera.ps1)。

```powershell
docker compose -f compose.yaml -f compose.camera.yaml down
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\reset-camera.ps1
```

复位脚本针对演示机器上的 HP 摄像头（BUSID `4-2`，硬件 ID `30c9:011d`）。它先解除 WSL 挂接，再取消 USB 共享。出现管理员确认时点「是」，看到 `Not shared` 即可开始下一轮演示。`usbipd`、WSL 驱动和 `compose.camera.yaml` 都可以继续用于下次配置。其他摄像头可用 `usbipd list` 查到自己的 BUSID，再用 `usbipd detach --busid 实际编号` 解除挂接，并在管理员 PowerShell 中用 `usbipd unbind --busid 实际编号` 取消共享。

### Linux 主机和容器设备映射

直接在 Linux 上运行 Docker 时，在宿主机上用 `ls -l /dev/video*` 确认设备。Windows 上的配置脚本会生成或复用下面的设备映射。

在项目根目录创建 `compose.camera.yaml`，与 `compose.yaml` 放在一起：

```yaml
services:
  app:
    devices:
      - "/dev/video0:/dev/video0"
```

左边是 Docker 所在的 Linux 环境里的设备，右边是容器里的设备。若实际采集节点是 `/dev/video2`，可以改成 `/dev/video2:/dev/video0`，界面仍选摄像头编号 **0**。映射格式见 [Docker Compose devices](https://docs.docker.com/reference/compose-file/services/#devices)。

### 手动构建和启动

首次构建镜像或代码有改动时，在项目根目录运行以下命令。

```bash
docker compose -f compose.yaml -f compose.camera.yaml build
```

有本地镜像后，启动容器并检查设备映射。

```bash
docker compose -f compose.yaml -f compose.camera.yaml up -d --no-build --pull never
docker compose -f compose.yaml -f compose.camera.yaml exec app ls -l /dev/video0
```

打开 noVNC，点击「打开摄像头」并输入 **0**。如果识别处于暂停或视频已经播完，再点「开始识别」或「重新播放」继续。也可以在 `compose.camera.yaml` 的 `app` 下加上 `environment`，设置 `SOURCE: camera` 和 `SOURCE_VALUE: "0"`，让程序启动时直接选择摄像头。

- **容器里没有 `/dev/video0`**：先检查 Linux 侧是否有设备节点，再确认启动时同时传入了两个 Compose 文件。重建容器时也要带上摄像头配置，单独 `restart` 不会给已有容器增加设备映射。
- **设备存在，但打不开或读不到画面**：检查选中的是可采集图像的节点。有些摄像头会产生多个 `/dev/videoN`，其中可能有元数据节点。也要检查设备是否被其他进程占用。
- **画面大面积发绿**：之前用 USB 摄像头经 WSL 转接时遇到过这个问题。程序已经在 V4L2 后端请求 `MJPG`，该摄像头换成这种格式后恢复。其他设备的表现取决于摄像头和驱动支持的格式。

停止带摄像头的容器可运行 `docker compose -f compose.yaml -f compose.camera.yaml down`。

## 操作与验收

1. 点击「开始识别」播放默认演示视频，观察检测框、类别和置信度。右侧通信日志按轮次展示发送、确认和完成消息。勾选「显示原始帧」可核对十六进制报文，确认分类计数在动作完成后更新。
2. 点击「导入视频」，在 Docker 界面中选择 `/workspace/vedio/dep.mp4`。拖动进度条、逐帧查看或回到开头时，界面会暂停预览，点击「开始识别」后继续分拣。
3. 按前面的设备映射说明打开摄像头，验证视频和实时输入可以切换。选择「清空日志」并确认后，通信面板清空，分类计数继续保留。
4. 在已安装项目依赖的环境中运行公开测试：

   ```bash
   python -m unittest discover -s tests -v
   ```

默认视频、Linux Docker 下的界面与模拟通信、视频跳转以及 10 项公开测试均已验证。真实串口和 Jetson Nano 部署需要在对应硬件上验收。评分、录屏、PPT 和仓库链接的提交要求见 [TASKS.md](TASKS.md)。

## 本机调试（可选）

仓库提供 [requirements-local.txt](requirements-local.txt)。安装依赖后，在项目根目录开两个终端。第一个运行模拟下位机：

```bash
python -m simulator.lower_simulator --host 127.0.0.1 --port 9000
```

第二个启动上位机：

```bash
python -m app.main --sim-host 127.0.0.1 --sim-port 9000
```

## Jetson Nano

[Dockerfile.nano](Dockerfile.nano) 面向经典 Jetson Nano 的 JetPack 4.x / L4T 32.x。在配好 NVIDIA 容器运行时的板卡上运行：

```bash
cat /etc/nv_tegra_release
docker compose -f compose.yaml -f compose.nano.yaml up --build -d
```

使用摄像头时按实际节点加入设备映射。若使用真实串口，在 Compose 的 `app` 服务中映射板卡的串口设备，例如 `/dev/ttyUSB0:/dev/ttyUSB0`，并设置 `TRANSPORT=serial`、`SERIAL_PORT=/dev/ttyUSB0` 和下位机实际使用的 `BAUDRATE`。默认波特率为 `9600`。
