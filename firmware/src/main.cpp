/*
  16 x 16 FSR 매트릭스 압력 센서 스캔 펌웨어
  Board  : ESP32-S3-DEVKITC-1
  MUX A  : Row 선택용 (SIG -> 3.3V, C0~C15 -> 각 Row 전극)
  MUX B  : Col 선택용 (SIG -> ADC 핀(풀다운 포함), C0~C15 -> 각 Col 전극)

  동작 원리:
    3.3V -> Row(선택됨) -> (r,c) 압력저항 -> Col(선택됨) -> 풀다운 R -> GND
    압력이 커질수록 저항이 낮아지고 ADC 전압이 상승한다.
    선택되지 않은 채널은 MUX 내부적으로 Hi-Z 이므로
    한 번에 하나의 Row/Col만 회로에 연결되어 크로스토크가 최소화된다.

  Serial 출력 형식 (1프레임 = 1줄):
    F,r0c0,r0c1, ... ,r15c15\n   (총 256개 값, row-major, 물리 좌표 기준)
*/

#include <Arduino.h>

// ---------------- 핀 설정 ----------------
const int muxA[4]  = {7, 6, 5, 4};     // MUX A(ROW) S0,S1,S2,S3
const int muxB[4]  = {13, 12, 11, 10}; // MUX B(COL) S0,S1,S2,S3
const int adcPin   = 9;                // MUX B의 SIG와 연결된 ADC 입력 핀

// ---------------- 물리 위치(index) -> 실제 MUX 채널 매핑 ----------------
// PCB 트레이스 라우팅으로 인해 채널 순서가 물리적 좌표와 다를 수 있음.
// 실측 후 필요하면 이 배열만 수정하면 됨.
const int rowChannel[16] = {
  7, 6, 5, 4, 3, 2, 1, 0,
  8, 9, 10, 11, 12, 13, 14, 15
};
const int colChannel[16] = {
  0, 1, 2, 3, 4, 5, 6, 7,
  8, 9, 10, 11, 12, 13, 14, 15
};

// ---------------- 스캔 파라미터 (튜닝 대상) ----------------
const int SAMPLES_PER_POINT = 4;   // 포인트당 ADC 평균 샘플 수 (노이즈 억제)
const int SETTLE_US         = 80;  // MUX 전환 후 안정화 대기시간 (us)
                                    // 케이블/트레이스가 길수록 값을 키워야 함

uint16_t pressure[16][16]; // [row][col] : 물리 좌표 기준 저장

// 4비트 select 라인에 채널 번호를 그대로 실어준다 (0~15)
void selectMuxChannel(const int* selPins, int channel) {
  for (int i = 0; i < 4; i++) {
    digitalWrite(selPins[i], (channel >> i) & 0x01);
  }
}

uint16_t readPoint(int rowIdx, int colIdx) {
  selectMuxChannel(muxA, rowChannel[rowIdx]);
  selectMuxChannel(muxB, colChannel[colIdx]);
  delayMicroseconds(SETTLE_US);

  uint32_t sum = 0;
  for (int s = 0; s < SAMPLES_PER_POINT; s++) {
    sum += analogRead(adcPin);
  }
  return (uint16_t)(sum / SAMPLES_PER_POINT);
}

void scanMatrix() {
  for (int r = 0; r < 16; r++) {
    for (int c = 0; c < 16; c++) {
      pressure[r][c] = readPoint(r, c);
    }
  }
}

void sendFrame() {
  Serial.print("F,");
  for (int r = 0; r < 16; r++) {
    for (int c = 0; c < 16; c++) {
      Serial.print(pressure[r][c]);
      if (!(r == 15 && c == 15)) Serial.print(',');
    }
  }
  Serial.println();
}

void setup() {
  Serial.begin(921600); // 256포인트를 빠르게 스트리밍하기 위해 고속 baud 사용
                         // PC측 시리얼 포트도 동일하게 맞출 것

  for (int i = 0; i < 4; i++) {
    pinMode(muxA[i], OUTPUT);
    pinMode(muxB[i], OUTPUT);
  }

  analogReadResolution(12);        // 0~4095
  analogSetAttenuation(ADC_11db);  // 0~3.3V 풀레인지 측정
  pinMode(adcPin, INPUT);
}

void loop() {
  scanMatrix();
  sendFrame();
  // 필요 시 delay(x)로 프레임레이트 제한 가능. 기본은 최대 속도로 연속 전송.
}
