#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include <map>

#define ALLOW_DELAY_CALLS false
#include <IRremoteESP8266.h>  // https://github.com/crankyoldgit/IRremoteESP8266
#include <ir_Kelon.h>

// Supported modes: AUTO_MODE, COOL_MODE, DRY_MODE, HEAT_MODE, FAN_MODE
// Supported fan: FAN_AUTO, FAN_MIN, FAN_MED, FAN_HI

// ESP8266 GPIO pin to use for IR blaster.
// GPIO2 == D2 on board. Look images/ESP8266-NodeMCU-pinout.webp for more information
const uint16_t kIrLed = 4;

IRKelonAc ac(kIrLed);

std::map<String, uint8_t> acConfigMap;

#ifndef STASSID
#define STASSID "<SSID>"
#define STAPSK "<PASSWORD>"
#endif

const char* ssid = STASSID;
const char* password = STAPSK;

ESP8266WebServer server(80);

const char* www_username = "???";
const char* www_password = "???";

const char* host = "ac-remote";

bool send = false;
bool power_toggle = false;
bool power = false;

void acAction(){
  if(!send)
    return;

  server.stop();
  WiFi.mode(WIFI_OFF);

  // ###### WIFI Free Zone ######
  // All IR transmitting only with WIFI turned off:
  // https://github.com/crankyoldgit/IRremoteESP8266/issues/2025
  // https://github.com/crankyoldgit/IRremoteESP8266/issues/1922
  if (power_toggle){
    ac.ensurePower(power);
  }
  if(power){
    ac.send();
  }
  // ######

  send = false;
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(WiFi.status());
  }
  server.begin();
}

int handleAc(StaticJsonDocument<200> &data){
  Serial.println("handleAc()");
  if (!data.containsKey("power_toggle")){
      return 1;
  }
  if (!data.containsKey("power")){
      return 2;
  }
  if (!data.containsKey("mode")){
      return 3;
  }
  if (!data.containsKey("fan")){
      return 4;
  }
  if (!data.containsKey("temperature")){
      return 5;
  }

  power = data["power"].as<bool>();
  power_toggle = data["power_toggle"].as<bool>();
  if (power){
    ac.setMode(acConfigMap[data["mode"].as<String>()]);
    ac.setFan(acConfigMap[data["fan"].as<String>()]);
    ac.setTemp(data["temperature"].as<int>() + 2);  // Hack since my AC have lower min temp
  }
  send = true;
  Serial.println("handleAc() -- FIN!");
  return 0;
}

void handleAcRoute() {
  Serial.println("handleAcRoute()");
  if (!server.authenticate(www_username, www_password)) {
      return server.requestAuthentication();
  }

  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method Not Allowed");
    return;
  }

  StaticJsonDocument<200> JSONData;
  String jsonString = server.arg("plain");
  DeserializationError error = deserializeJson(JSONData, jsonString);
  if (error) {
    Serial.print(F("deserializeJson() failed: "));
    Serial.println(error.f_str());
    server.send(500, "application/json", "Error in parsing");
    delay(50);
    return;
  }
  Serial.println("ready to handleAc() with data: " + JSONData.as<String>());
  if(handleAc(JSONData)){
    server.send(400, "application/json", "Error handling JSON");
    delay(50);
  } else {
    server.send(200, "application/json", "OK");
    delay(50);
  }
}

void setup(void) {
  acConfigMap["COOL_MODE"] = kKelonModeCool;
  acConfigMap["HEAT_MODE"] = kKelonModeHeat;
  acConfigMap["FAN_MODE"]  = kKelonModeFan;

  acConfigMap["FAN_AUTO"] = 0;
  acConfigMap["FAN_MIN"]  = kKelonFanMin;
  acConfigMap["FAN_MED"]  = kKelonFanMedium;
  acConfigMap["FAN_HI"]   = kKelonFanMax;

  Serial.begin(115200);

  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);
    WiFi.setPhyMode(WIFI_PHY_MODE_11G);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
  Serial.println("");

  // Wait for connection
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(WiFi.status());
  }
  Serial.println("");
  Serial.print("Connected to ");
  Serial.println(ssid);
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  server.on("/ac", handleAcRoute);

  server.begin();

  Serial.print("Open http://");
  Serial.println(WiFi.localIP());

  ac.begin();
}

void loop() {
  server.handleClient();
  acAction();
}