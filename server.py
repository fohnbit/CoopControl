#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import requests
from socket import *
from threading import Thread 
import _thread
import pytz
import time
import sys
import Adafruit_DHT
import glob
import datetime
import RPi.GPIO as GPIO
from pushover import Pushover
import json
import yaml
import smbus
from http.server import BaseHTTPRequestHandler, HTTPServer

# from astral import Astral

# Hold either button for 2 seconds to switch modes
# In auto buttons Stop for 60 seconds. Again, continues
# In manual, left goes up assuming it's not up. right goes down assuming
#  any button while moving stops it
# Todo:
# Record how long it takes to open the door, close
# ERror states


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler('/tmp/log.log')
fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

fh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(ch)



class Coop(BaseHTTPRequestHandler):
    file = open('config.yml', 'r')
    CFG = yaml.load(file, Loader=yaml.FullLoader)
    
    file = open('userSettings.yml', 'r')
    USR = yaml.load(file, Loader=yaml.FullLoader)

    MAX_MANUAL_MODE_TIME = USR.get("times").get("max_manual_mode_time")
    MAX_MOTOR_ON = USR.get("times").get("max_motor_on_time")
    SENSOR_INTERVAL = USR.get("times").get("sensor_read_interval")
    # TIMEZONE_CITY = 'Boston'
    AFTER_SUNSET_DELAY = USR.get("times").get("after_sunset_delay")
    AFTER_SUNRISE_DELAY = USR.get("times").get("after_sunrise_delay")
    SECOND_CHANCE_DELAY = USR.get("times").get("second_chance_delay")
    
    
    
    IDLE = UNKNOWN = NOT_TRIGGERED = AUTO = 0
    UP = OPEN = TRIGGERED = MANUAL = 1
    DOWN = CLOSED = HALT = 2

    PIN_LED = CFG.get("pins").get("led")
    PIN_BUTTON_UP = CFG.get("pins").get("button_up")
    PIN_BUTTON_DOWN = CFG.get("pins").get("button_down")
    PIN_SENSOR_TOP = CFG.get("pins").get("sensor_top")
    PIN_SENSOR_BOTTOM = CFG.get("pins").get("sensor_bottom")
    PIN_MOTOR_ENABLE = CFG.get("pins").get("motor_enable")
    PIN_MOTOR_A = CFG.get("pins").get("motor_a")
    PIN_MOTOR_B = CFG.get("pins").get("motor_b")

    ONE_WIRE_WATER = '/sys/bus/w1/devices/' + CFG.get("one_wire").get("water") + '/w1_slave'
    ONE_WIRE_WATER2 = '/sys/bus/w1/devices/' + CFG.get("one_wire").get("water2") + '/w1_slave'
    PIN_TEMP_WATER = 4 # Can't change
    PIN_TEMP1 = 22
    PIN_TEMP2 = 6
    
    file = open('auth.yml', 'r')
    AUTH = yaml.load(file, Loader=yaml.FullLoader)
    
    PUSHOVER_TOKEN = AUTH.get("auth").get("api-key")
    PUSHOVER_USER = AUTH.get("auth").get("user-key")  

    HOST_NAME = '192.168.0.63'
    PORT_NUMBER = 9000

    def __init__(self):
        
        self.indoor_dawn_treshold = Coop.USR.get("treshold").get("indoor_dawn")
        self.indoor_dawn_hysteresis = Coop.USR.get("treshold").get("indoor_dawn_hysteresis")
        self.indoor_food_treshold = Coop.USR.get("treshold").get("indoor_food")
        self.indoor_food_hysteresis = Coop.USR.get("treshold").get("indoor_food_hysteresis")
        self.indoor_light_hours = Coop.USR.get("times").get("light_hours") # wie lange muss das Licht mind. brennen
        self.indoor_dimming_time = Coop.USR.get("times").get("dimming_time")

        self.door_status = Coop.UNKNOWN
        self.started_motor = None 
        self.direction = Coop.IDLE
        self.door_mode = Coop.AUTO
        self.manual_mode_start = 0
        self.temp_water = 0
        self.second_chance = True
        self.indoor_temp = 0
        self.outdoor_temp = 0
        self.indoor_humidity = 0
        self.outdoor_humidity = 0        
        self.indoor_illumination = 0
        self.outdoor_illumination = 0
        self.food_illumination = 0
        self.indoor_light_1 = 0
        self.indoor_light_2 = 0
        self.indoor_light_3 = 0
        self.indoor_light_4 = 0
        self.indoor_light_5 = 0
        self.indoor_light_6 = 0
        self.cache = {}


        #self.mail_key = os.environ.get('MAILGUN_KEY') or exit('You need a key set')
        #self.mail_url = os.environ.get('MAILGUN_URL') or exit('You need a url set')
        #self.mail_recipient = os.environ.get('MAILGUN_RECIPIENT') or exit('You need a recipient set')

        #try:
        #    base_dir = '/sys/bus/w1/devices/'
        #    device_folder = glob.glob(base_dir + '28*')[0]
        #    self.device_file = device_folder + '/w1_slave'
        #except:
        #    self.device_file = None
        #    pass

        #a = Astral()
        #self.city = a[Coop.TIMEZONE_CITY]
        self.setupPins()

        t1 = Thread(target = self.checkTriggers)
        #t2 = Thread(target = self.checkSensors)
        t3 = Thread(target = self.readTemps)
        t1.setDaemon(True)
        #t2.setDaemon(True)
        t3.setDaemon(True)
        t1.start()
        #t2.start()
        t3.start()

        host = '192.168.0.63'
        port = 55567
        addr = (host, port)

        serversocket = socket(AF_INET, SOCK_STREAM)
        serversocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        serversocket.bind(addr)
        serversocket.listen(2)

        self.changeDoorMode(Coop.AUTO)
        self.stopDoor(0)

        GPIO.add_event_detect(Coop.PIN_BUTTON_UP, GPIO.RISING, callback=self.buttonPress, bouncetime=200)
        GPIO.add_event_detect(Coop.PIN_BUTTON_DOWN, GPIO.RISING, callback=self.buttonPress, bouncetime=200)
    
    
        # init Webserver
        
        server_class = HTTPServer
        httpd = server_class((Coop.HOST_NAME, Coop.PORT_NUMBER), Coop)
        print(time.asctime(), 'Server Starts - %s:%s' % (Coop.HOST_NAME, Coop.PORT_NUMBER))
        httpd.serve_forever()
    
        while True:
            try:
                logger.info("Server is listening for connections\n")
                clientsocket, clientaddr = serversocket.accept()
                _thread.start_new_thread(self.handler, (clientsocket, clientaddr))
            except KeyboardInterrupt:
                break
            time.sleep(0.01)

        logger.info("Close connection")
        httpd.server_close()
        GPIO.output(Coop.PIN_LED, GPIO.LOW)
        serversocket.close()
        self.stopDoor(0)

    def setupPins(self):
        GPIO.setmode(GPIO.BCM)

        GPIO.setup(Coop.PIN_MOTOR_ENABLE, GPIO.OUT)
        GPIO.setup(Coop.PIN_MOTOR_A, GPIO.OUT)
        GPIO.setup(Coop.PIN_MOTOR_B, GPIO.OUT)
        GPIO.setup(Coop.PIN_LED, GPIO.OUT)
        GPIO.setup(Coop.PIN_SENSOR_BOTTOM, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(Coop.PIN_SENSOR_TOP, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(Coop.PIN_BUTTON_UP, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(Coop.PIN_BUTTON_DOWN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        
        # self.emergencyStopDoor('Startup Sequenz')

    def closeDoor(self):
        (top, bottom) = self.currentTriggerStatus()
        print (top, bottom)
        if (bottom == Coop.TRIGGERED):
            logger.info("Door is already closed")
            return
        logger.info("Closing door")
        self.started_motor = datetime.datetime.now()
        GPIO.output(Coop.PIN_MOTOR_ENABLE, GPIO.HIGH)
        GPIO.output(Coop.PIN_MOTOR_A, GPIO.LOW)
        GPIO.output(Coop.PIN_MOTOR_B, GPIO.HIGH)
        self.direction = Coop.DOWN

    def openDoor(self):
        (top, bottom) = self.currentTriggerStatus()
        if (top == Coop.TRIGGERED):
            logger.info("Door is already open")
            return
        logger.info("Opening door")
        self.started_motor = datetime.datetime.now()
        GPIO.output(Coop.PIN_MOTOR_ENABLE, GPIO.HIGH)
        print(Coop.PIN_MOTOR_A)
        GPIO.output(Coop.PIN_MOTOR_A, GPIO.HIGH)
        GPIO.output(Coop.PIN_MOTOR_B, GPIO.LOW)
        self.direction= Coop.UP

    def stopDoor(self, delay):
        if self.direction != Coop.IDLE:
            logger.info("Stop door")
            time.sleep(delay)
            GPIO.output(Coop.PIN_MOTOR_ENABLE, GPIO.LOW)
            GPIO.output(Coop.PIN_MOTOR_A, GPIO.LOW)
            GPIO.output(Coop.PIN_MOTOR_B, GPIO.LOW)
            self.direction = Coop.IDLE
            self.started_motor = None

        (top, bottom) = self.currentTriggerStatus()
        if (top == Coop.TRIGGERED):
            logger.info("Door is open")
            self.door_status = Coop.OPEN
            self.pushOver('Coop door is OPEN')
        elif (bottom == Coop.TRIGGERED):
            logger.info("Door is closed")
            self.door_status = Coop.CLOSED
            self.pushOver('Coop door is CLOSED')
        else:
            logger.info("Door is in an unknown state")
            self.door_status = Coop.UNKNOWN

            # payload = {'status': self.door_status, 'ts': datetime.datetime.now() }
            # self.postData('door', payload)

    def emergencyStopDoor(self, reason):
        ## Just shut it off no matter what
        logger.info("Emergency Stop door: " + str(reason))
        GPIO.output(Coop.PIN_MOTOR_ENABLE, GPIO.LOW)
        GPIO.output(Coop.PIN_MOTOR_A, GPIO.LOW)
        GPIO.output(Coop.PIN_MOTOR_B, GPIO.LOW)
        self.direction = Coop.IDLE
        self.started_motor = None
        self.changeDoorMode(Coop.HALT)
        self.stopDoor(0)
        self.pushOver('Coop Emergency STOP: ' + str(reason))

    #def sendEmail(self, subject, content):
    #    logger.info("Sending email: %s" % subject)
    #    try:
    #        request = requests.post(
    #            self.mail_url,
    #            auth=("api", self.mail_key),
    #            data={"from": "Chickens <mailgun@mailgun.dxxd.net>",
    #                  "to": [self.mail_recipient],
    #                  "subject": subject,
    #                  "text": content}) 
    #        #logger.info('Status: {0}'.format(request.status_code))
    #    except Exception as e:
    #        logger.error("Error: " + e)

    def postData(self, endpoint, payload):
        try:
            r = requests.post("http://ryandetzel.com:3000/api/" + endpoint, data=payload)
        except Exception as e:
            logger.error(e)

    #def checkTime(self):
        #while True:
            #if self.door_mode == Coop.AUTO:
                #current = datetime.datetime.now(pytz.timezone(self.city.timezone))
                #sun = self.city.sun(date=datetime.datetime.now(), local=True)

                #after_sunset = sun["sunset"] + datetime.timedelta(minutes = Coop.AFTER_SUNSET_DELAY)
                #after_sunrise = sun["sunrise"] + datetime.timedelta(minutes = Coop.AFTER_SUNRISE_DELAY) 

                #if (current < after_sunrise or current > after_sunset) and self.door_status != Coop.CLOSED and self.direction != Coop.DOWN:
                #    logger.info("Door should be closed based on time of day")
                #    self.closeDoor()

                #    if self.second_chance:
                #        t2 = Thread(target = self.secondChance)
                #        t2.setDaemon(True)
                #        t2.start()
                #elif current > after_sunrise and current < after_sunset and self.door_status != Coop.OPEN and self.direction != Coop.UP:
                #    logger.info("Door should be open based on time of day")
                #    self.openDoor()
           # time.sleep(1)

    def readTempRaw(self, file):
        f = open(file, 'r')
        lines = f.readlines()
        f.close()
        return lines

    def waterTemp(self):
        if Coop.ONE_WIRE_WATER is None:
            return
        lines = self.readTempRaw(Coop.ONE_WIRE_WATER)

        while lines[0].strip()[-3:] != 'YES':
            time.sleep(0.2)
            lines = self.readTempRaw()
        equals_pos = lines[1].find('t=')
        if equals_pos != -1:
            temp_string = lines[1][equals_pos+2:]
            temp_c = float(temp_string) / 1000.0
            self.temp_water = temp_c
            logger.info("Water temp: %f" % temp_c)


            # payload = {'name': 'water', 'temperature': temp_c, 'humidity': 0, 'ts': datetime.datetime.now() }
            # self.postData('temperature', payload)

    def tempForPin(self, pin):
        retries = 3
        humidity, temperature = Adafruit_DHT.read_retry(Adafruit_DHT.AM2302, pin)
        while (humidity is None or temperature is None) and retries > 0:
            time.sleep(1)
            humidity, temperature = Adafruit_DHT.read_retry(Adafruit_DHT.AM2302, pin)
            retries -= 1

        if humidity is not None and temperature is not None:
            temp_f = temperature * 9.0 / 5.0 + 32.0
            #if cache[pin] and abs(cache[pin] - temp_f) > 20:
            #    retries -= 1
            #    continue
            logger.info('Temp={0:0.1f}*F  Humidity={1:0.1f}%'.format(temp_f, humidity))
            return temp_f, humidity

        logger.error('Failed to get reading temp. Try again!')
        return (0, 0)

    def otherTemps(self):
        if Coop.ONE_WIRE_WATER2 is None:
            return
        lines = self.readTempRaw(Coop.ONE_WIRE_WATER2)

        while lines[0].strip()[-3:] != 'YES':
            time.sleep(0.2)
            lines = self.readTempRaw()
        equals_pos = lines[1].find('t=')
        if equals_pos != -1:
            temp_string = lines[1][equals_pos+2:]
            temp_c = float(temp_string) / 1000.0
            self.temp_water = temp_c
            logger.info("Water2 temp: %f" % temp_c)
            
        #(self.temp1, self.humidity1) = self.tempForPin(Coop.PIN_TEMP1)
        #(self.temp2, self.humidity2) = self.tempForPin(Coop.PIN_TEMP2)
        
        #ts = datetime.datetime.now()
        # payload = {'name': 'temp1', 'temperature': self.temp1, 'humidity': self.humidity1, 'ts': ts}
        # self.postData('temperature', payload)

        # payload = {'name': 'temp2', 'temperature': self.temp2, 'humidity': self.humidity2, 'ts': ts }
        # self.postData('temperature', payload)

    def illumination(self):
        address = 0x48
        A0 = 0x40
        A1 = 0x41
        A2 = 0xA2
        A3 = 0xA3
        bus = smbus.SMBus(1)

        bus.write_byte(address,A0)	
        bus.read_byte(address)
        self.outdoor_illumination = bus.read_byte(address)
        logger.info("Outdoor Illumination: %f" % self.outdoor_illumination)
        bus.write_byte(address,A1)	
        bus.read_byte(address)
        self.indoor_illumination = bus.read_byte(address)
        logger.info("Indoor Illumination: %f" % self.indoor_illumination)
        bus.write_byte(address,A2)	
        bus.read_byte(address)
        self.food_illumination = bus.read_byte(address)  
        logger.info("Food Illumination: %f" % self.food_illumination)        


    def readTemps(self):
        while True:
            self.illumination()
            self.waterTemp()
            self.otherTemps()
            time.sleep(Coop.SENSOR_INTERVAL)

    def currentTriggerStatus(self):
        bottom = not GPIO.input(Coop.PIN_SENSOR_BOTTOM)
        top = not GPIO.input(Coop.PIN_SENSOR_TOP)
        return (top, bottom)

    def checkTriggers(self):
        while True:
            (top, bottom) = self.currentTriggerStatus()
            # print(top, bottom)
            if (self.direction == Coop.UP and top == Coop.TRIGGERED):
                logger.info("Top sensor triggered")
                self.stopDoor(0)
            if (self.direction == Coop.DOWN and bottom == Coop.TRIGGERED):
                logger.info("Bottom sensor triggered")
                self.stopDoor(1)

            # Check for issues
            if self.started_motor is not None:
                if (datetime.datetime.now() - self.started_motor).seconds > Coop.MAX_MOTOR_ON:
                    self.emergencyStopDoor('Motor ran too long')

            time.sleep(0.01)

    def changeDoorMode(self, new_mode):
        if new_mode == self.door_mode:
            logger.info("Already in that mode")
            return

        if new_mode == Coop.AUTO:
            logger.info("Entered auto mode")
            self.door_mode = Coop.AUTO
            GPIO.output(Coop.PIN_LED, GPIO.HIGH)
        else:
            logger.info("Entered manual mode")
            self.door_mode = new_mode
            self.stopDoor(0)
            self.manual_mode_start = int(time.time())

            t2 = Thread(target = self.blink)
            t2.setDaemon(True)
            t2.start()

    def buttonPress(self, button):
        waiting = True
        start = end = int(round(time.time() * 1000))

        while GPIO.input(button) and waiting:
            end = int(round(time.time() * 1000))
            if end - start >= 2000:
                if self.door_mode == Coop.AUTO:
                    self.changeDoorMode(Coop.MANUAL)
                else:
                    self.changeDoorMode(Coop.AUTO)
                time.sleep(2)
                waiting = False
                return
            time.sleep(0.1)

        # Quick touch, what mode?
        if self.door_mode == Coop.MANUAL:
            if self.direction != Coop.IDLE:
                self.stopDoor(0)
            elif (button == Coop.PIN_BUTTON_UP):
                self.openDoor()
            else:
                self.closeDoor()

    def secondChance(self):
        logger.info("Starting second chance timer")
        time.sleep(Coop.SECOND_CHANCE_DELAY)
        if self.door_status == Coop.CLOSED or self.door_status == Coop.UNKNOWN:
            logger.info("Opening door for second chance")
            self.openDoor()
            time.sleep(Coop.SECOND_CHANCE_DELAY)
            logger.info("Closing door for the night")
            self.closeDoor()

    def blink(self):
        while(self.door_mode != Coop.AUTO):
            GPIO.output(Coop.PIN_LED, GPIO.LOW)
            time.sleep(1)
            GPIO.output(Coop.PIN_LED, GPIO.HIGH)
            time.sleep(1)
            if self.door_mode == Coop.MANUAL: 
                if int(time.time()) - self.manual_mode_start > Coop.MAX_MANUAL_MODE_TIME:
                    logger.info("In manual mode too long, switching")
                    self.changeDoorMode(Coop.AUTO)


    def handler(self, clientsocket, clientaddr):
        #logger.info("Accepted connection from: %s " % clientaddr)

        while True:
            data = clientsocket.recv(1024).decode()
            if not data:
                break
            else:
                data = data.strip()
                if (data == 'stop'):
                    self.changeDoorMode(Coop.MANUAL)
                    self.stopDoor(0)
                elif (data == 'open'):
                    print ('open now')
                    self.changeDoorMode(Coop.MANUAL)
                    self.openDoor()
                elif (data == 'close'):
                    self.changeDoorMode(Coop.MANUAL)
                    self.closeDoor()
                elif (data == 'manual'):
                    self.changeDoorMode(Coop.MANUAL)
                elif (data == 'auto'):
                    self.changeDoorMode(Coop.AUTO)
                elif (data == 'halt'):
                    self.changeDoorMode(Coop.HALT)
                elif (data == 'status_request'):
                    clientsocket.sendall(str.encode(self.createJson()))
                elif (data == 'get_log'):
                    f = open('/tmp/log.log', 'r')
                    clientsocket.sendall(str.encode(f.read()))
                    f.close()
                    
                #msg = "You sent me: %s" % data
                #clientsocket.send(msg)
            time.sleep(0.01)
        clientsocket.close()
    
    def pushOver(self, message):
        try:
            po = Pushover(Coop.PUSHOVER_TOKEN)
            po.user(Coop.PUSHOVER_USER)
            msg = po.msg(message)
            msg.set("title", "Pips´s kleine Hühnerfarm")
            # po.send(msg)
        except Exception as e:
            logger.error(e)
    
       
    def createJson(self):
        data = {}
        data['door_state'] = self.door_status
        data['indoor_temp'] = self.indoor_temp
        data['indoor_humidity'] = self.indoor_humidity
        data['indoor_illumination'] = self.indoor_illumination
        data['indoor_light_1'] = self.indoor_light_1
        data['indoor_light_2'] = self.indoor_light_2
        data['indoor_light_3'] = self.indoor_light_3
        data['indoor_light_4'] = self.indoor_light_4
        data['indoor_light_5'] = self.indoor_light_5
        data['indoor_light_6'] = self.indoor_light_6
        data['indoor_water_temp'] = self.temp_water
        data['food_illumination'] = self.food_illumination
        
        data['outdoor_temp'] = self.outdoor_temp
        data['outdoor_humidity'] = self.outdoor_humidity
        data['outdoor_illumination'] = self.outdoor_illumination
        
        data['indoor_dawn_treshold'] = self.indoor_dawn_treshold
        data['indoor_dawn_hysteresis'] = self.indoor_dawn_hysteresis
        
        data['indoor_food_treshold'] = self.indoor_food_treshold
        data['indoor_food_hysteresis'] = self.indoor_food_hysteresis
        
        
        data['indoor_light_hours'] = self.indoor_light_hours
        data['indoor_dimming_time'] = self.indoor_dimming_time
        
        json_data = json.dumps(data)
        
        return json_data
    
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        paths = {
            '/foo': {'status': 200},
            '/bar': {'status': 302},
            '/baz': {'status': 404},
            '/qux': {'status': 500}
        }

        if self.path in paths:
            self.respond(paths[self.path])
        else:
            self.respond({'status': 500})

    def handle_http(self, status_code, path):
        self.send_response(status_code)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        content = '''
        <html><head><title>Title goes here.</title></head>
        <body><p>This is a test.</p>
        <p>You accessed path: {}</p>
        </body></html>
        '''.format(path)
        return bytes(content, 'UTF-8')

    def respond(self, opts):
        response = self.handle_http(opts['status'], self.path)
        self.wfile.write(response)
    
if __name__ == "__main__":   
    coop = Coop()
