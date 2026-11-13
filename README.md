# 智能垃圾分拣上位机

本项目用 OpenCV 读取视频或摄像头画面，用 YOLO 权重 `Predict/best.pt` 识别目标并映射到四类垃圾。分拣控制器根据连续帧判定是否发送指令，收到下位机的 `ACTION_DONE` 后才更新计数。普通电脑可以用 Docker 和 TCP 模拟下位机完成演示。通信帧定义见 [PROTOCOL.md](PROTOCOL.md)。

## Docker 启动

在装有 Docker Engine 和 Compose 的 Linux 电脑上，或开启了 **WSL 2 后端并使用 Linux 容器**的 Windows Docker Desktop 上，从项目根目录运行：

```bash
docker compose up --build -d
docker compose ps
```

浏览器打开 [noVNC 操作界面](http://localhost:6080/vnc.html?autoconnect=1&resize=scale)。默认会播放 `assets/assessment_demo.mp4`，并连接容器网络内的模拟下位机 `lower-sim:9000`。该模式无需摄像头或实体串口。修改源码后用 `--build` 重新构建。如果只是再次启动已构建的版本，可以用 `docker compose up -d`。

```bash
docker compose logs --tail 100 app lower-sim
docker compose down
```

## 在 Docker 中使用摄像头

需要先让 Docker 所在的 Linux 环境识别摄像头，出现 `/dev/videoN`，再用 Compose 将该设备映射进 `app` 容器。noVNC 只负责显示容器里的界面，浏览器的摄像头权限不会把设备交给容器。

### Windows：Docker Desktop + WSL 2

1. 启动 Docker Desktop 并启用 WSL 2 后端。安装 [usbipd-win](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)，安装后重新打开 PowerShell。

   ```powershell
   winget install --interactive --exact dorssel.usbipd-win
   ```

2. 关闭 Windows 上占用摄像头的应用。在**管理员 PowerShell** 里运行下面的命令，从 `usbipd list` 找到要连接的摄像头，把实际的 BUSID 输入进去。已经显示为 `Shared` 的设备可以跳过 `bind`。执行 `attach` 时应有一个正在运行的 WSL 2 环境。

   ```powershell
   usbipd list
   $cameraBusId = Read-Host '输入摄像头的 BUSID'
   usbipd bind --busid $cameraBusId
   usbipd attach --wsl --busid $cameraBusId
   usbipd list
   ```

3. 确认摄像头显示为 `Attached` 后，检查 Docker 所在的 Linux 环境是否有视频设备：

   ```powershell
   wsl -d docker-desktop -- sh -c 'ls -l /dev/video*'
   ```

   如果已经 `Attached` 但没有视频节点，可尝试加载 UVC 驱动，再检查一次：

   ```powershell
   wsl -d docker-desktop -u root -- modprobe uvcvideo
   wsl -d docker-desktop -- sh -c 'ls -l /dev/video*'
   ```

   若驱动无法加载或仍没有视频节点，当前 WSL 内核或摄像头可能不支持这套配置。可以先用默认视频验收，或用 Windows 本机模式测试摄像头。摄像头连接到 WSL 期间，Windows 应用无法同时使用它。用完后可运行 `usbipd detach --busid $cameraBusId` 将设备交还给 Windows，重插或重启后可能需要重新执行 `attach`。
   
   ```powershell
   docker compose -f compose.yaml -f compose.camera.yaml up -d
   ```

### Linux 主机和容器设备映射

直接在 Linux 上运行 Docker 时，跳过 USB/IP 步骤，在宿主机上用 `ls -l /dev/video*` 确认设备。Windows 完成 WSL 转接后也从这里继续。

在项目根目录创建 `compose.camera.yaml`，与 `compose.yaml` 放在一起：

```yaml
services:
  app:
    devices:
      - "/dev/video0:/dev/video0"
```

左边是 Docker 所在的 Linux 环境里的设备，右边是容器里的设备。若实际采集节点是 `/dev/video2`，可以改成 `/dev/video2:/dev/video0`，界面仍选摄像头编号 **0**。映射格式见 [Docker Compose devices](https://docs.docker.com/reference/compose-file/services/#devices)。

```bash
docker compose -f compose.yaml -f compose.camera.yaml up --build -d
docker compose -f compose.yaml -f compose.camera.yaml exec app ls -l /dev/video0
```

打开 noVNC，点击「打开摄像头」并输入 **0**。如果识别处于暂停或视频已经播完，再点「开始识别」或「重新播放」继续。也可以在 `compose.camera.yaml` 的 `app` 下加上 `environment`，设置 `SOURCE: camera` 和 `SOURCE_VALUE: "0"`，让程序启动时直接选择摄像头。

- **容器里没有 `/dev/video0`**：先检查 Linux 侧是否有设备节点，再确认启动时同时传入了两个 Compose 文件。重建容器时也要带上摄像头配置，单独 `restart` 不会给已有容器增加设备映射。
- **设备存在，但打不开或读不到画面**：检查选中的是可采集图像的节点。有些摄像头会产生多个 `/dev/videoN`，其中可能有元数据节点。也要检查设备是否被其他进程占用。
- **画面大面积发绿**：之前用 USB 摄像头经 WSL 转接时遇到过这个问题。程序已经在 V4L2 后端请求 `MJPG`，该摄像头换成这种格式后恢复。其他设备的表现取决于摄像头和驱动支持的格式。

停止带摄像头的容器可运行 `docker compose -f compose.yaml -f compose.camera.yaml down`。

## 操作与验收

1. 播放默认演示视频，观察检测框、类别和置信度。右侧通信日志按轮次展示发送、确认和完成消息。勾选「显示原始帧」可核对十六进制报文，确认分类计数在动作完成后更新。
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
python -m app.main --sim-host 127.0.0.1 --sim-port 9000 --auto-start
```

## Jetson Nano

[Dockerfile.nano](Dockerfile.nano) 面向经典 Jetson Nano 的 JetPack 4.x / L4T 32.x。在配好 NVIDIA 容器运行时的板卡上运行：

```bash
cat /etc/nv_tegra_release
docker compose -f compose.yaml -f compose.nano.yaml up --build -d
```

使用摄像头时按实际节点加入设备映射。若使用真实串口，在 Compose 的 `app` 服务中映射板卡的串口设备，例如 `/dev/ttyUSB0:/dev/ttyUSB0`，并设置 `TRANSPORT=serial`、`SERIAL_PORT=/dev/ttyUSB0` 和下位机实际使用的 `BAUDRATE`。默认波特率为 `9600`。
