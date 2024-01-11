#!/usr/bin/python3

import logging
import time
import socketserver
import simplejpeg
import cv2
from http import server
from threading import Condition, Thread
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder, Quality
from picamera2.outputs import FfmpegOutput


CLIPLENGTH = 60 # In seconds
FRAMERATE = 20.0 # FPS
OUTPUTPATH = "/your/path/here/" # Replace path
RECORDWITH, RECORDHEIGHT = 1920, 1080 # Resolution of recorded files
STREAMWIDTH, STREAMHEIGHT = 960, 540 # Resolution of stream
STREAMPORT = 8000
TEXTCOLOR = (255, 255, 255)  # White timestamp text color
BORDERCOLOR = (0, 0, 0) # Black timestamp border
FONT = cv2.FONT_HERSHEY_DUPLEX
SCALE = 1
THICKNESS = 2
BORDERTHICKNESS = 5
PAGE = """\
<!DOCTYPE html>
<html>
<head>
<title>Picamera2 MJPEG streaming</title>
</head>
<body>
<h1>Stream:</h1>
<img src="stream.mjpg" width="{0}" height="{1}" alt="Streaming Image">
</body>
</html>
""".format(STREAMWIDTH, STREAMHEIGHT)

def stream_encode():
    global mjpeg_frame
    while not mjpeg_abort:
        yuv = picam2.capture_array("lores")
        rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV420p2RGB)
        buf = simplejpeg.encode_jpeg(rgb, quality=100, colorspace='BGR', colorsubsampling='420')
        with mjpeg_condition:
            mjpeg_frame = buf
            mjpeg_condition.notify_all()
            time.sleep(1)

class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with mjpeg_condition:
                        mjpeg_condition.wait()
                        frame = mjpeg_frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def text_overlay(request):
    timestamp = time.strftime("%d.%m.%Y %X")
    with MappedArray(request, "main") as m:
            height, width, _ = m.array.shape
            text_size = cv2.getTextSize(timestamp, FONT, SCALE, THICKNESS)[0]
            text_x = (
                width - text_size[0] - 20
            )
            text_y = height - 20

            # Draw the timestamp with a black border
            cv2.putText(
                m.array, timestamp, (text_x, text_y), FONT, SCALE, BORDERCOLOR, BORDERTHICKNESS + THICKNESS,
            )

            # Draw the actual timestamp text in white
            cv2.putText(
                m.array, timestamp, (text_x, text_y), FONT, SCALE, TEXTCOLOR, THICKNESS
            )

def h264_encode():
    while True:
        file_name = OUTPUTPATH+time.strftime("%Y-%m-%d_%H_%M_%S")
        format = ".mp4"
        output = FfmpegOutput(file_name + format)
        encoder = H264Encoder()
        QUALITY = Quality.VERY_HIGH
        picam2.start_and_record_video(
            output, encoder, duration=CLIPLENGTH, quality=QUALITY
        )    

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (RECORDWITH, RECORDHEIGHT)}, controls={"FrameRate": FRAMERATE}, lores={"size": (STREAMWIDTH, STREAMHEIGHT)}, encode="main"))
picam2.pre_callback = text_overlay

try:
    picam2.start()

    mjpeg_abort = False
    mjpeg_frame = None
    mjpeg_condition = Condition()
    mjpeg_thread = Thread(target=stream_encode, daemon=True)
    mjpeg_thread.start()

    encode_abort = False
    encode_thread = Thread(target=h264_encode, daemon=False)
    encode_thread.start()

    address = ('', STREAMPORT)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
    mjpeg_thread.join()
    encode_thread.join()
finally:
    picam2.stop()