# Iron Cow RK3568 NAS → Armbian + OpenMediaVault

Guía paso a paso, completa y probada de punta a punta, para reemplazar el firmware
de fábrica (con backdoor de "phone home") de un NAS "Iron Cow" basado en RK3568 por
Armbian + OpenMediaVault, usando una Raspberry Pi como consola de debug y puente de
transferencia. Incluye todos los problemas reales encontrados en el camino y cómo
se resolvieron, no solo los comandos que funcionaron al final.

Este proceso se hizo íntegramente por consola serial UART, sin necesidad de abrir
la carcasa más allá de acceder al header de debug, y sin arriesgar el eMMC hasta
validar todo primero por USB.

> **Nota:** todos los valores como IPs, UUIDs, nombres de usuario y
> contraseñas en esta guía son placeholders genéricos (`<ip-del-nas>`,
> `<uuid>`, `/dev/sdX`, etc.). Reemplazalos siempre por los datos reales de
> tu propio equipo — nunca copies un valor de esta guía como si fuera literal.

## Por qué

El firmware de fábrica de este NAS:
- Llama a `portal.iron-cow-ainas.com` (y variantes) en cada boot, incluso sin
  configuración de usuario — telemetría/phone-home no solicitado.
- Corre un túnel VPN de acceso remoto (`tunsvr`) habilitado por defecto.
- Tiene un daemon de monitoreo (`nasMonitor.py`) adicional.
- Su eMMC además mostraba errores de I/O reales en el arranque (hardware
  defectuoso, no filesystem corrupto — confirmado con `e2fsck`).

## Crédito

El device tree parcheado para este NAS (`rk3568-nas.dtb`) viene del repositorio
público de **gloriouspidgeon855**: https://github.com/gloriouspidgeon855/IroncowNASArmbian
Esta guía documenta el proceso completo alrededor de ese device tree: cómo llegar
a él, cómo probarlo de forma segura, y cómo dejarlo arrancando automáticamente
desde el eMMC interno con OpenMediaVault instalado y funcionando.

## Qué necesitás

- El NAS Iron Cow (RK3568), con acceso físico al header de debug UART interno.
- Una Raspberry Pi (se usó una Pi 5) para hacer de consola serial y puente de red.
- 3 cables jumper hembra-hembra.
- Una microSD o pendrive USB de al menos 8GB para las pruebas (no se toca el eMMC
  hasta el final).
- Un cable Ethernet directo (o adaptador USB-Ethernet) entre la Pi y el NAS para
  transferir archivos grandes sin depender de red compartida.

---

## Parte 1 — Consola serial UART

**Cableado (con el NAS apagado):**

| Pi (GPIO header) | NAS (header de debug) |
|---|---|
| Pin 8 (GPIO14 / TXD) | RX |
| Pin 10 (GPIO15 / RXD) | TX |
| Pin 6 (GND) | GND |

**No conectar el pin de 3.3V.**

**En la Pi:**

```bash
sudo raspi-config
# Interface Options → Serial Port
#   "login shell over serial" → No
#   "hardware serial port" → Yes
# Reboot
```

Confirmá que aparece `/dev/serial0` (o `/dev/ttyAMA0`) después del reboot.

```bash
sudo apt install screen
sudo screen /dev/serial0 1500000
```

Prendé el NAS y deberías ver el log de arranque del boot ROM / U-Boot / Debian.
Texto ilegible = cableado invertido o baudrate incorrecto. Sin nada = revisar
cableado/alimentación.

---

## Parte 2 — Entrar a U-Boot sin botón de recovery físico

El botón de recovery físico (pinhole trasero) **no funcionó** en ningún intento
en este NAS — no lo asumas como primera opción.

El U-Boot de este NAS (Rockchip vendor U-Boot 2017.09) tiene `bootdelay=0`
(sin ventana de reacción humana), pero como respeta `CONFIG_ZERO_BOOTDELAY_CHECK`,
un byte Ctrl+C que ya esté esperando en el buffer RX del UART en el instante exacto
del boot sí lo detiene.

**Técnica:** un script Python (pyserial) que manda `\x03` (Ctrl+C) en loop cerrado
durante ~45s mientras el NAS se enciende (o reinicia) *durante* esa ventana.

Ver [`scripts/uboot_break.py`](scripts/uboot_break.py). Uso:

```bash
python3 scripts/uboot_break.py
# el script avisa cuando empezar — ahí es cuando hay que enchufar el power del NAS
```

Vas a ver `=> <INTERRUPT>` repetido en la salida cuando lo logra — eso es el
prompt de U-Boot (`=>`).

Si el NAS ya está encendido y corriendo Linux (no apagado), usá en cambio
[`scripts/reboot_and_break.py`](scripts/reboot_and_break.py), que dispara el
reboot vía `sysrq` **antes** de empezar el flood — importante: arrancar el flood
*antes* de mandar el trigger de reboot, no después, porque el apagado de systemd
puede tardar varios segundos impredecibles y comerse toda la ventana si el flood
arranca tarde.

---

## Parte 3 — Acceso root persistente, sin saber la password

Desde el prompt `=>` de U-Boot:

```
setenv bootargs "${bootargs} init=/bin/sh"
boot
```

Esto bootea directo a una shell root (`/bin/sh` como PID 1), bypaseando el login
por completo. Es un cambio de un solo boot (no se hizo `saveenv`), así que no
toca nada persistente del eMMC.

El entorno es mínimo — no hay `/proc`, `/sys`, ni `/etc/mtab` montados:

```sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
```

Confirmá que el root (`/`, típicamente `mmcblk0p6` en este modelo) está montado
`rw` (lo estaba en este caso pese al parámetro `ro` del kernel cmdline, que este
U-Boot ignora). Después:

```sh
passwd          # setear password de root
sync
reboot -f
```

Desde ese momento, login normal como `root` con la password que pusiste, sin
volver a necesitar la danza de U-Boot para acceso básico.

---

## Parte 4 — Salud del eMMC

El firmware de fábrica mostraba errores reales de I/O en boot
(`blk_update_request: I/O error`, `mmc0: Timeout waiting for hardware interrupt`,
`cache flush error -110`). Antes de asumir que el filesystem estaba corrupto:

```sh
e2fsck -n /dev/mmcblk0p6     # -n: solo reporta, no modifica nada
```

En este caso: 0 errores estructurales en las 5 pasadas. Los errores de boot eran
**flakiness del controlador eMMC a nivel hardware**, no corrupción de filesystem
— no arreglable con fsck, pero suficientemente estable para arrancar el SO
mientras los datos reales del NAS vivan en discos SATA, no en el eMMC.

---

## Parte 5 — Desactivar el phone-home / backdoor de fábrica

En `/etc/rc.local` del firmware de fábrica había tres procesos de
telemetría/acceso remoto lanzados en cada boot:

- `check_rtcp.sh` → llama cada 5min a `check_rtcp.lua`, que hace `curl` a una URL
  de "portal" (históricamente `portal.iron-cow-ainas.com`, con un valor viejo
  comentado `portal.wocyber.com` como posible vendor relacionado).
- `tunsvr /etc/server.ini` → el túnel VPN de acceso remoto.
- `nasMonitor.py` → telemetría/monitoreo.

Se desactivaron comentando sus líneas de lanzamiento en `/etc/rc.local` (efecto
tras reboot). Los logs de nginx (`/usr/local/openresty/nginx/logs/error.log`)
mostraban que esta unidad específica **había estado pareada con la nube antes**
(flujo de login QR contra `us-portal.iron-cow-ainas.com`) — el estado de ese
pareo puede seguir vivo del lado del servidor del fabricante aunque localmente
ya no llame a nada.

**Ojo con esto también (no tocado, pero a tener en cuenta):** `rc.local` hace
`rm -rf /disk0/* /disk1/* /disk2/*` en cada boot. Inofensivo con discos SATA
vacíos/desconectados, pero destructivo si alguna vez bootea el firmware de
fábrica con discos reales montados ahí sin querer.

Esta parte es relevante solo si vas a seguir usando el firmware de fábrica un
tiempo antes de reemplazarlo. Si vas directo a Armbian, podés saltarla.

---

## Parte 6 — Conseguir el device tree de Armbian

En vez de reversear el device tree desde cero, usá el ya construido y probado
por la comunidad: https://github.com/gloriouspidgeon855/IroncowNASArmbian

Archivos clave de ese repo:
- `rk3568-nas.dtb` / `.dts` — compilado contra kernel 6.12, derivado del `.dtb`
  de fábrica comparado con el `.dts` mainline del QNAP TS433 (hardware similar).
- `armbianEnv.txt` / `extlinux.conf` — configs de boot. **El `UUID=` de ambos hay
  que editarlo para que coincida con el UUID real de la partición root de la
  imagen que termines usando** — sin esto el board no bootea (cae a emergency
  shell). El README de ese repo lo aclara también.
- `armbianOnAUSBStick.md` — documenta probar por USB antes de tocar el eMMC, y
  confirma la misma técnica de Ctrl+C-flood usada acá.
- Nota sobre el puerto USB del NAS: arranca en modo *device* (cliente) por
  defecto y hay que pasarlo a modo *host* para que reconozca un pendrive:
  ```sh
  echo host > /sys/kernel/debug/usb/fcc00000.dwc3/mode
  ```
  (path alternativo visto en `rc.local` del firmware de fábrica:
  `/sys/devices/platform/fe8a0000.usb2-phy/otg_mode` — puede depender de la
  revisión de placa, probar ambos si uno no existe).
- `unspyware.sh` de ese repo **no usar tal cual**: pese al nombre, *habilita*
  `tunsvr`/`check_rtcp.lua` en vez de desactivarlos — es lo opuesto de la Parte 5.

Descargá también una imagen Armbian actual para el board más parecido
disponible (se usó **Odroid M1S**, que también es RK3568) desde
armbian.com — Debian 13 (trixie) mínima.

---

## Parte 7 — Probar primero por USB, nunca directo al eMMC

**No toques el eMMC sin haber arrancado Armbian con éxito desde un pendrive o
microSD USB primero.**

1. Escribí la imagen de Armbian descargada a un pendrive/microSD vía USB:
   ```sh
   xzcat armbian_....img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
   ```
2. **Verificá checksums del archivo recién escrito contra el original,
   siempre.** En este proceso, un `dd`/pipe que "termina sin error" **no** es
   garantía de integridad — se encontró corrupción silenciosa real en una
   escritura así (ver Parte 9, mismo patrón repetido dos veces en esta guía).
   Montá la partición del pendrive y comparná `md5sum` archivo por archivo
   contra el original (podés loop-mount el `.img` decomprimido con
   `udisksctl loop-setup -f archivo.img`, sin sudo).
3. Copiá `rk3568-nas.dtb`, `armbianEnv.txt` (con el UUID corregido a la
   partición real del pendrive) y `extlinux/extlinux.conf` (mismo UUID, y
   `FDT /rk3568-nas.dtb` apuntando al dtb) al `/boot` del pendrive.
4. Conectá el pendrive al puerto USB del NAS (recordá pasarlo a modo host,
   Parte 6).
5. Entrá a U-Boot (Parte 2) y probá el boot manual:
   ```
   run distro_bootcmd
   ```
   Si `distro_bootcmd` no encuentra el kernel automáticamente (nombres de
   archivo distintos entre versiones de Armbian — a veces es `vmlinuz` plano, a
   veces un symlink a `vmlinuz-<version>`), cargalo manual:
   ```
   load usb 0:1 <addr> /boot/<archivo-real>
   ...
   booti <kernel_addr> <initrd_addr> <fdt_addr>
   ```

### Bug real encontrado acá (y cómo diagnosticarlo)

Si `load`/`ext4load` reporta el tamaño de bytes correcto pero el kernel/initrd
no arrancan (o el contenido cargado en RAM es garbage), **no asumas que es un
bug del driver ext4 de este U-Boot** — antes de nada, verificá con
`md.b <addr> 40` que los primeros bytes cargados tengan el magic header
correcto (`d0 0d fe ed` para un `.dtb`, cabecera ELF/ARM64 para el kernel). En
este proceso, la causa real terminó siendo que el archivo en el disco de origen
ya estaba corrupto por una escritura previa mal verificada — no un bug de
U-Boot. Recopiar desde una fuente limpia y re-verificar el checksum resolvió
todo sin tocar la lógica de partición.

Si después de verificar los archivos siguen sin cargar bien vía ext4, como
alternativa más robusta agregá una **partición FAT32 pequeña** (~256MB) para
los archivos de boot (kernel/initrd/dtb/extlinux.conf) — el driver FAT de
U-Boot es mucho más maduro que su driver ext4, y es el layout que usan la
mayoría de boards RK3568 reales:

```sh
parted /dev/sdX mkpart primary fat32 <inicio> <fin>
mkfs.vfat -F 32 -n ARMBIBOOT /dev/sdXN
```

Con el boot confirmado (`Machine model: Rockchip RK3568 NAS`, SATA/red/USB
probando limpio, llega al wizard de primer boot de Armbian) — el software está
validado. `reboot` desde Armbian por USB vuelve al firmware de fábrica en el
eMMC sin tocarlo (nada se guardó con `saveenv`).

---

## Parte 8 — Flashear al eMMC interno

Con el software ya validado por USB, ahora sí al almacenamiento interno.

**Reparticionar `/dev/mmcblk0`** (hacerlo desde Armbian booteado por USB, no
desde el firmware de fábrica — su rootfs puede desaparecer a mitad del proceso
si borrás su propia partición):

```sh
parted /dev/mmcblk0 print free    # ver layout actual antes de asumir nada
# Conservar particiones 1-5 (uboot/misc/boot/recovery/backup, primeros ~240MiB)
# Borrar las de datos de fábrica (rootfs/oem/userdata) y crear:
#   partición nueva FAT32 ~256MB (boot)
#   partición nueva ext4 con el resto (~100% del espacio, no un valor exacto
#     en MiB — GPT necesita margen para la tabla de respaldo al final del disco)
```

**Reboot obligatorio** después de terminar todos los cambios de `parted` y
antes de hacer `mkfs`/`dd` en las particiones nuevas — la vista del kernel de
la tabla de particiones queda desactualizada tras varios cambios seguidos sin
reboot, y da error "attempt to access beyond end of device" aunque el GPT en
disco ya esté correcto.

**Formatear y copiar** (desde Armbian-por-USB, shell root vía el truco
`init=/bin/sh`):

```sh
mkfs.vfat -F 32 /dev/mmcblk0pN      # partición boot nueva
mkfs.ext4 /dev/mmcblk0pM            # partición root nueva

# copiar el rootfs completo del sistema corriendo (USB) al nuevo root:
# rsync -aHAX puede segfaultear en este initramfs mínimo — usar tar en su lugar
tar --exclude=/proc --exclude=/sys --exclude=/dev --exclude=/mnt \
    -cf - -C / . | tar -xf - -C /mnt/newroot/
```

Actualizá los UUID que quedaron grabados en el árbol copiado
(`/etc/fstab`, `/boot/armbianEnv.txt`, `/boot/extlinux/extlinux.conf`) del
UUID del pendrive al UUID real de la nueva partición root del eMMC (`sed`).

**Los archivos de boot van en la raíz plana de la partición FAT** (no bajo
`/boot/`) si `extlinux.conf` los referencia sin `/boot/` — confirmá el layout
real con `fatls` antes de asumir que replica la estructura del pendrive.

**Usar `fatload`, no `load` genérico, para mmc interno:**
```
fatload mmc 0:N <addr> Image
fatload mmc 0:N <addr> uInitrd
fatload mmc 0:N <addr> rk3568-nas.dtb
```
(en este NAS, `load mmc 0:N ... /boot/Image` fallaba con "Unable to read file"
pese a que `fatls` mostraba el archivo — `fatload` explícito funcionó de
inmediato.)

**Bootargs para el eMMC:** `root=UUID=<uuid-real-del-root-nuevo>` — no
`root=/dev/sda1` (eso era correcto solo para la prueba por USB).

Con eso cargado y verificado (`md.b`, siempre), `booti <kernel> <initrd> <fdt>`
debería llegar a un login limpio.

### Arranque automático sin intervención manual (la parte difícil)

`saveenv` en este U-Boot vendor **no persiste nada** — no tiene backend de
almacenamiento de entorno configurado (`CONFIG_ENV_IS_NOWHERE`, confirmable
con `env info`). Cualquier `setenv` solo dura la sesión actual de U-Boot.

El `bootcmd` real de este board es:
```
boot_android ${devtype} ${devnum};boot_fit;bootrkp;run distro_bootcmd;
```
(encadenado con `;`, corre cada uno sin condicional). `boot_fit` lee una
**imagen FIT grabada en la partición 3 ("boot", 64MiB)** — el kernel de
fábrica. Si esa partición no arranca algo válido (por ejemplo porque borraste
su root partition), el control nunca pasa a `distro_bootcmd`.

**La solución real es reemplazar el contenido de esa partición FIT** con una
construida para Armbian, usando `mkimage` (de `u-boot-tools`, viene con
Armbian):

1. Escribí un `.its` describiendo `kernel`+`fdt`+`ramdisk`
   (`/incbin/("/boot/vmlinuz-...")`, con los nombres de archivo reales, no
   symlinks; y el `initrd.img-*` crudo para el ramdisk, no el `uInitrd-*` ya
   envuelto por U-Boot), más un nodo `configurations` con:
   ```
   bootargs = "root=UUID=<uuid> rootdelay=10 rw console=tty1 console=ttyS2,1500000n8 earlycon=uart8250,mmio32,0xfe660000 cma=256M";
   ```
   **Embeber `bootargs` directo en el FIT es lo que lo hace funcionar sin
   entorno persistente de U-Boot** — `boot_fit` usa lo que trae el FIT, no
   necesita `setenv bootargs` previo.
2. `mkimage -f armbian.its armbian.itb`
3. `dd if=armbian.itb of=/dev/mmcblk0p3 bs=1M conv=fsync` — **paso genuinamente
   riesgoso** (sobreescribe el payload de boot de fábrica). Confirmá que la
   imagen `.itb` entra en los 64MiB de la partición antes de escribir.
4. El FIT de fábrica original estaba firmado (`sha256,rsa2048:dev`). El
   reemplazo sin firmar hace que `boot_fit` falle **limpio** (no cuelga) —
   justo lo necesario para que el `bootcmd` encadenado por `;` siga a
   `bootrkp` y después a `distro_bootcmd`. No hace falta firmar nada.

Aun con `boot_fit` fallando limpio, puede que `distro_bootcmd` todavía no
arranque solo — dos causas típicas, ambas confirmadas en este proceso:

- **`scan_dev_for_boot_part`** solo escanea particiones con el flag GPT
  **bootable** puesto (si no hay ninguna marcada, cae a la partición 1 fija).
  Fix:
  ```sh
  parted /dev/mmcblk0 set N boot on
  ```
  (cambio solo de metadata, no toca datos, bajo riesgo).
- **Paths de `extlinux.conf` sin `/` inicial se resuelven relativos a la
  carpeta de `extlinux.conf` misma** (`/extlinux/`), no a la raíz de la
  partición, cuando el scanner automático de distro-boot los procesa (distinto
  del `fatload` manual, que sí toma paths literales). Si tus archivos de boot
  viven en la raíz de la partición FAT (no bajo `/boot/`), usá paths absolutos
  con `/` inicial en `extlinux.conf`:
  ```
  LINUX /Image
  INITRD /uInitrd
  FDT /rk3568-nas.dtb
  ```

**Verificá el arranque automático de dos formas antes de darlo por bueno:**
- `sync; echo b > /proc/sysrq-trigger` (reboot por software, instantáneo, salta
  la secuencia de apagado de systemd).
- Un power-cycle físico real (desenchufar/enchufar el cable de alimentación).

Ambos deberían llegar al prompt de login **sin ninguna intervención manual de
U-Boot**.

---

## Parte 9 — Instalar OpenMediaVault

Con Armbian arrancando solo desde el eMMC:

```sh
apt-get update && apt-get upgrade -y
wget -O - https://github.com/OpenMediaVault-Plugin-Developers/installScript/raw/master/preinstall | bash
reboot
wget -O - https://github.com/OpenMediaVault-Plugin-Developers/installScript/raw/master/install | bash
```

OMV 8 soporta Debian 13 (trixie) oficialmente — guía de referencia:
https://wiki.omv-extras.org/doku.php?id=omv8:armbian_trixie_install

El script principal de instalación corre ~20-25 minutos y **termina
reiniciando solo** — no esperes que vuelva un prompt de shell, esperá la
secuencia de reboot en la consola serial.

### Trampa: desincronización de kernel entre partición boot y root

Si tu setup usa una **partición de boot separada** (FAT32, como en la Parte 8)
en vez de que U-Boot lea directo de la partición root, un `apt upgrade`
normal actualiza el kernel/initrd **dentro de la partición root**
(`/boot/vmlinuz-*`, `/boot/uInitrd-*`), pero **no sabe nada de tu partición
FAT separada** — que se queda con la versión vieja indefinidamente. El sistema
sigue arrancando (con el kernel viejo pero válido), pero los módulos del
kernel instalados por `apt` (en `/lib/modules/<version-nueva>/`) no
corresponden al kernel que realmente está corriendo — cualquier cosa que
dependa de un módulo (por ejemplo `md_mod` para RAID por software, o incluso
`nls_iso8859-1` para el propio driver vfat) va a fallar con
"Module X not found in directory /lib/modules/<version-vieja-que-ya-no-tiene-carpeta>".

**Verificar el desajuste:**
```sh
uname -r                     # kernel realmente corriendo
ls /lib/modules/             # kernels con módulos instalados
```
Si no coinciden, hay que sincronizar manualmente después de cada
`apt upgrade` que toque el kernel:

```sh
cp /boot/vmlinuz-<version-nueva> /ruta/a/particion-boot/Image
cp /boot/uInitrd-<version-nueva> /ruta/a/particion-boot/uInitrd
# el dtb normalmente no cambia entre actualizaciones de kernel, pero
# verificar el checksum igual
md5sum /boot/vmlinuz-<nueva> /ruta/a/particion-boot/Image   # confirmar copia limpia
```

Si la partición FAT no monta desde el Linux corriendo por el mismo motivo
(kernel viejo sin el módulo `nls_iso8859-1` para el driver vfat), usá
`mtools` (`mcopy`/`mdir`, sin necesidad de montar) como alternativa de
espacio de usuario:
```sh
apt-get install -y mtools
echo 'drive m: file="/dev/mmcblkXpN"' > /etc/mtools.conf
mdir -i /dev/mmcblkXpN ::/
mcopy -o -i /dev/mmcblkXpN /boot/vmlinuz-<nueva> ::/Image
```

Después de sincronizar y rebootear al kernel correcto, la partición FAT vuelve
a montar normalmente (el kernel nuevo sí trae todos sus módulos), simplificando
sincronizaciones futuras.

---

## Parte 10 — RAID 1 (opcional, si tenés 2+ discos)

Con `md_mod` cargando bien (Parte 9 resuelta si hacía falta):

```sh
apt-get install -y openmediavault-md    # plugin de RAID de OMV, no viene por defecto
```

En el firmware, el servicio RPC correspondiente es `MdMgmt` (no `RaidMgmt`,
pese a que la sección del GUI puede llamarse "RAID Management").

Si los discos ya tienen un filesystem creado por vos vía CLI en vez del wizard
de OMV, primero hay que sacarlos de la config de OMV (`Storage → File Systems`,
unmount, y `omv-confdbadm delete --uuid <uuid> conf.system.filesystem.mountpoint`)
antes de que `mdadm` pueda tomar los discos crudos.

```sh
wipefs -a /dev/sdX /dev/sdY
mdadm --create /dev/md0 --level=1 --raid-devices=2 --metadata=1.2 /dev/sdX /dev/sdY --run
mdadm --detail --scan >> /etc/mdadm/mdadm.conf
update-initramfs -u
mkfs.ext4 -L raid1data /dev/md0
```

Se puede formatear y usar el array mientras el resync inicial corre en segundo
plano (`cat /proc/mdstat`) — no hace falta esperar a que termine.

Registrar el filesystem resultante en OMV: `Storage → File Systems → + →
Mount existing file system`, elegir `/dev/md0`.

---

## Parte 11 — Carpeta compartida SMB

1. `Storage → Shared Folders → +` — elegir el filesystem, nombre de carpeta.
2. `Services → SMB/CIFS → Settings` — activar el servicio.
3. `Services → SMB/CIFS → Shares → +` — asociar la shared folder creada.
4. `Users → Users → +` — crear un usuario con password (esto también crea su
   cuenta Samba automáticamente) y darle permisos read/write sobre la shared
   folder.

Acceso desde otra máquina: `smb://<ip-del-nas>/<nombre-share>`.

---

## Parte 12 — Acceso remoto fuera de la red local

**No expongas SMB ni el panel de OMV directo a internet** — es exactamente el
tipo de superficie de ataque que se eliminó en la Parte 5. La opción segura es
una VPN mesh:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --hostname=<nombre-que-quieras>
# imprime una URL de login — abrirla en un navegador ya logueado a tu cuenta
# Tailscale para autorizar el dispositivo
```

Con eso, el NAS obtiene una IP fija de Tailscale (`100.x.x.x`) accesible desde
cualquier dispositivo conectado a la misma tailnet, sin abrir puertos en el
router. `tailscale status` confirma la conexión; el servicio queda habilitado
para arrancar solo en cada boot (`systemctl is-enabled tailscaled`).

---

## Resumen de herramientas incluidas

| Script | Uso |
|---|---|
| [`scripts/uboot_break.py`](scripts/uboot_break.py) | Flood de Ctrl+C durante power-on para interrumpir el autoboot de U-Boot (Parte 2) |
| [`scripts/reboot_and_break.py`](scripts/reboot_and_break.py) | Igual, pero dispara el reboot vía `sysrq` primero — para cuando el NAS ya está corriendo Linux |
| [`scripts/tcp_recv.py`](scripts/tcp_recv.py) | Servidor TCP crudo (sin HTTP) para recibir archivos grandes desde el NAS cuando no hay `nc`/`sshd` disponibles de ningún lado |

Todos requieren `pyserial` (`pip install pyserial`) para los dos primeros, y
Python 3 estándar para el tercero. Ajustar `PORT` en los scripts de U-Boot si
tu adaptador serial no es `/dev/ttyAMA0`.

## Advertencia

Esta guía involucra escribir directo a particiones de firmware/boot de bajo
nivel (`dd` a `/dev/mmcblk0pN`, reemplazo de imagen FIT). Un error en la
Parte 8 puede dejar el NAS sin arrancar. Mitigación real usada en este proceso:
**siempre validar todo por USB primero (Parte 7)**, y guardar un backup de la
zona crítica de boot del eMMC antes de tocarlo:

```sh
dd if=/dev/mmcblk0 bs=4M count=150 | gzip -c > nas_boot_backup.img.gz
```

(600MB cubre de sobra el área raw de idbloader/U-Boot más las particiones 1-5
— es lo único que un flasheo de Armbian realmente sobreescribe o pone en
riesgo; el rootfs de fábrica en sí no se consideró necesario respaldar por no
tener datos únicos).
