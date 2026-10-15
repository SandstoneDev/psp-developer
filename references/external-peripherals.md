# Talking to external hardware (ESP32 and similar) from PSP homebrew

Which physical channels a retail PSP offers a homebrew author, what each costs, and which are closed. API facts come
from the public pspsdk headers; microcontroller-side facts are general knowledge and flagged as such; the end-to-end
numbers come from a link we built and ran [measured: PSP-3000, homebrew test program].

## Channel table

| channel | PSP role | pspsdk API | verdict |
|---|---|---|---|
| WiFi infrastructure (join an access point) | client | `pspnet_apctl.h`, `pspnet_inet.h` | **start here**, no extra hardware |
| WiFi ad-hoc | peer | `pspnet_adhoc.h`, `pspnet_adhocctl.h` | not a generic 802.11 link (below) |
| USB, PSP as device | device | `pspusbbus.h` (`sceUsbbdRegister`) | workable; the other end must be a USB **host** |
| USB, PSP as host | host | none | closed: no host API in pspsdk (`pspusbacc.h` only names the accessory driver) |
| Remote-port serial (UART) | either | `pspDebugSio*` in libpspdebug (kernel mode) | works; needs a kernel module and a connector |
| Headphone-remote keys | input | `psphprm.h` | key bits and presence flags only, not a data channel |

## Why ad-hoc is not the obvious answer

`pspnet_adhocctl.h` is in pspsdk, so "PSP ad-hoc + ESP32" looks natural. The signatures say otherwise
[public: pspsdk src/net/pspnet_adhocctl.h]:

```c
int sceNetAdhocctlInit(int stacksize, int priority, struct productStruct *product);
int sceNetAdhocctlConnect(const char *name);
int sceNetAdhocctlCreateEnterGameMode(const char *name, int unknown, int num,
                                      unsigned char *macs, unsigned int timeout, int unknown2);
```

Groups are scoped to a product ID (`productStruct`), and the matching and control protocol above 802.11 is the
firmware's own; that is what ad-hoc server projects (PPSSPP's included) re-implement [general knowledge]. A
microcontroller speaking plain 802.11 has nothing to join.

**Use infrastructure mode instead**: the microcontroller raises a SoftAP, the PSP joins it as an ordinary client, and
above that you have ordinary sockets. Nothing proprietary in the path.

## Infrastructure mode

Module loading [public: pspsdk src/utility/psputility_netmodules.h]:
```c
#define PSP_NET_MODULE_COMMON 1
#define PSP_NET_MODULE_INET   3
int sceUtilityLoadNetModule(int module);      /* COMMON first, then INET */
```

Stack bring-up [public: pspsdk src/net/pspnet.h, pspnet_inet.h, pspnet_apctl.h]:
```c
int sceNetInit(int poolsize, int calloutprio, int calloutstack,
               int netintrprio, int netintrstack);
int sceNetInetInit(void);
int sceNetApctlInit(int stackSize, int initPriority);
int sceNetApctlConnect(int connIndex);        /* index of a saved network profile */
int sceNetApctlGetState(int *pState);
int sceNetApctlGetInfo(int code, SceNetApctlInfo *pInfo);
```

States to poll (`PSP_NET_APCTL_STATE_*`): 0 disconnected, 1 scanning, 2 joining, 3 getting IP, 4 got IP, 5 EAP
auth, 6 key exchange [public: pspsdk src/net/pspnet_apctl.h]. pspsdk's `samples/net/simple` shows the whole
bring-up.

Sockets are BSD calls with an `sceNetInet` prefix: `sceNetInetSocket`, `sceNetInetBind`, `sceNetInetSendto`,
`sceNetInetRecvfrom`, `sceNetInetClose`, `sceNetInetGetErrno` [public: pspsdk src/net/pspnet_inet.h].

### The part that is not in code

`sceNetApctlConnect(connIndex)` connects to a **profile saved in the PSP's own network settings**. A homebrew program
cannot create one, so anything that talks to your own access point needs the user to create a profile first.
`PSP_NET_APCTL_INFO_PROFILE_NAME` confirms at run time which profile you joined [public: pspsdk
src/net/pspnet_apctl.h].

### Radio constraints that bite on the microcontroller side

[general knowledge, verify on your hardware]
- The PSP radio is 802.11b, 2.4 GHz. An access point forced to g/n-only, or on channel 12-14, can be invisible to it.
- pspsdk's security enum is `0 none / 1 WEP / 2 WPA` (`PSP_NET_APCTL_INFO_SECURITY_TYPE_*`) [public: pspsdk
  src/net/pspnet_apctl.h]; a microcontroller SoftAP usually defaults to WPA2-PSK. For a bench prototype start with an
  open access point to remove the variable, then add WPA.
- An open access point is fine on a bench and wrong for anything that carries real input or controls anything: any
  device in range can join and inject. Put a key on it, and authenticate the application protocol too [practice].

## Measured end to end

A PSP-3000-class console joined an ESP32 SoftAP and ran a UDP protocol with a mutual HMAC-SHA256 handshake
[measured: PSP-3000, homebrew test program]:

```
auto-selected profile #4 (default was #1)
  state: joining        569 ms
  state: getting ip    2781 ms
  state: got ip        3792 ms     <- association + DHCP = 3.2 s
  -> HELLO             3820 ms
  <- CHALLENGE ok      3868 ms
  -> CONFIRM           3872 ms     <- mutual handshake = 56 ms
```

Microcontroller side of the same run: 1 client, 107 authenticated UDP packets, 0 rejected, over ~53 s.

**Budget ~3 s for association, not milliseconds.** `sceNetApctlConnect` -> got IP took 3.2 s with DHCP on an idle
open access point. Do not put that behind a 1-second timeout.

### Three traps, each one hardware round trip

1. **`sceNetApctlConnect(n)` takes a profile index, not "the first match".** The console had four saved profiles and
   the wanted one was #4; index 1 was the user's home network, so the PSP joined the wrong SSID and bounced
   joining -> disconnected, which looks exactly like a broken access point. Enumerate and match by SSID:
   `sceUtilityCheckNetParam(i)` + `sceUtilityGetNetParam(i, PSP_NETPARAM_SSID, &d)` over the saved profiles, then
   connect to the index whose SSID matches [public: pspsdk src/utility/psputility_netparam.h]. Also seen:
   `PSP_NETPARAM_SECURE` returned 4, a value outside the 0/1/2 set; print raw values, do not assume an enum is
   complete.
2. **A log that is only closed at exit stays empty.** The file size is recorded on close, so a log opened at boot and
   closed at exit was 0 bytes on every run that did not reach the exit path, including the hung ones. Commit with
   open-append-write-close at each state change, close after the setup phase, and also close in the exit callback,
   because HOME calls `sceKernelExitGame()` past your cleanup. See [debug-tools.md](debug-tools.md) section 7.
3. **The debug-screen font is ASCII only.** Non-ASCII log strings printed with `pspDebugScreenPrintf` came out as
   garbage blocks. Keep anything that reaches the screen ASCII; the same applies to a serial console whose terminal
   is not UTF-8.

Method: photographing the PSP screen was unreadable and cost a round trip. Write the log to `ms0:` next to the EBOOT
and read it on the PC [practice].

## USB, PSP as device

`pspusbbus.h` exposes the **bus-device** side [public: pspsdk src/usb/pspusbbus.h]:
```c
int sceUsbbdRegister(struct UsbDriver *drv);
int sceUsbbdReqSend(struct UsbdDeviceReq *req);
int sceUsbbdReqRecv(struct UsbdDeviceReq *req);
int sceUsbbdStall(struct UsbEndpoint *endp);
```
You register a custom USB class with your own endpoints and a host enumerates you. PSPLink's `usbhostfs` is built
this way, so there is a working open implementation to copy [public: psplinkusb usbhostfs/main.c].

So the microcontroller must be the **host** [general knowledge]:
- A classic ESP32 has no USB peripheral; the USB connector on typical boards goes to a USB-UART bridge, which is
  itself a USB device. It cannot host.
- ESP32-S2 / S3 have native USB OTG and can run host mode.
- A classic ESP32 plus a MAX3421E host controller also works.

A cable between a PSP and such a board connects two device ports, with no host and no VBUS source: nothing
enumerates. Explain that first when someone proposes it.

## Remote-port UART

`psphprm.h` gives only key bits (`PSP_HPRM_PLAYPAUSE`, `FORWARD`, `BACK`, `VOL_UP`, `VOL_DOWN`, `HOLD`) and presence
checks (`sceHprmIsRemoteExist`, `sceHprmIsHeadphoneExist`, `sceHprmIsMicrophoneExist`) [public: pspsdk
src/hprm/psphprm.h]. Not a byte pipe.

Behind that connector is a real UART. pspsdk's debug library drives it: `pspDebugSioInit` stops the headphone-remote
driver, enables UART4 and switches on the remote-port power; the default is 4800 baud 8N1 and `pspDebugSioSetBaud`
goes up to 115200; `pspDebugSioPutchar` / `pspDebugSioGetchar` poll the FIFO registers directly [public: pspsdk
src/debug/sio.c, samples/debug/sio]. That is register access, so it runs in kernel mode (a kernel PRX on current
firmware). Logic levels are 3.3 V class, which suits an ESP32 [general knowledge, verify before wiring]. Cost: the
kernel module plus a remote-port connector or soldering. Worth it only for low latency or no radio.

## Recommended order

1. WiFi infrastructure + microcontroller SoftAP + UDP: no extra hardware, both ends are ordinary sockets.
2. UART, if you later need lower latency or no radio.
3. USB, only if you need bandwidth, and only with a host-capable microcontroller: you write a USB class on both ends.

## Sources

- pspsdk: https://github.com/pspdev/pspsdk (`src/net/pspnet.h`, `pspnet_inet.h`, `pspnet_apctl.h`,
  `pspnet_adhocctl.h`, `src/utility/psputility_netmodules.h`, `psputility_netparam.h`, `src/usb/pspusbbus.h`,
  `pspusbacc.h`, `src/hprm/psphprm.h`, `src/debug/sio.c`, `src/samples/debug/sio`, `src/samples/net/simple`)
- psplinkusb (usbhostfs as a USB device-class example): https://github.com/pspdev/psplinkusb
