import serial
import time
import sys

PORT = '/dev/ttyAMA0'
BAUD = 1500000
FLOOD_SECONDS = 45

ser = serial.Serial(PORT, BAUD, timeout=0)
ser.reset_input_buffer()

print(f"Flooding Ctrl+C for {FLOOD_SECONDS}s. PLUG IN THE NAS'S POWER NOW.", flush=True)

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

print("\n\n--- Ctrl+C flood done, listening for 5 more seconds ---", flush=True)
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
print("\n--- Done. Full log at /home/koko/uboot_break.log ---", flush=True)
