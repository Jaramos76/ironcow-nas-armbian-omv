import serial
import time
import sys

PORT = '/dev/ttyAMA0'
BAUD = 1500000
FLOOD_SECONDS = 45

ser = serial.Serial(PORT, BAUD, timeout=0)
ser.reset_input_buffer()

# trigger reboot via sysrq (works even if userspace is degraded)
ser.write(b'echo b > /proc/sysrq-trigger\r\n')
time.sleep(0.3)

log = open('/home/koko/uboot_break.log', 'wb')
start = time.time()
while time.time() - start < FLOOD_SECONDS:
    ser.write(b'\x03')
    data = ser.read(4096)
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        log.write(data)
        log.flush()
    time.sleep(0.02)

print("\n\n--- Fin del flood, escuchando 5s mas ---", flush=True)
end2 = time.time() + 5
while time.time() < end2:
    data = ser.read(4096)
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        log.write(data)
        log.flush()
    time.sleep(0.02)

log.close()
ser.close()
print("\n--- Listo ---", flush=True)
