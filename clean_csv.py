import csv
import sys
import os

def clean_krx_csv():
    input_file = 'data/krx_universe.csv'
    output_file = 'data/krx_universe_clean.csv'
    
    if not os.path.exists(input_file):
        print(f"❌ 파일이 없습니다: {input_file}")
        return
    
    # 다양한 인코딩 시도
    encodings = ['utf-8-sig', 'cp949', 'utf-8', 'euc-kr']
    content = None
    for enc in encodings:
        try:
            with open(input_file, 'r', encoding=enc) as f:
                content = f.read()
            print(f"✅ 인코딩 성공: {enc}")
            break
        except:
            continue
    
    if content is None:
        print("❌ 모든 인코딩 실패")
        return
    
    lines = content.splitlines()
    if not lines:
        print("❌ 파일이 비어있습니다.")
        return
    
    # CSV 파싱 (여러 구분자 시도)
    delimiters = [',', '\t', ';']
    rows = None
    for delim in delimiters:
        try:
            reader = csv.reader(lines, delimiter=delim, quotechar='"')
            rows = list(reader)
            if rows and len(rows[0]) >= 2:
                print(f"✅ 구분자 감지: '{delim}'")
                break
        except:
            continue
    
    if not rows or not rows[0] or len(rows[0]) < 2:
        print("❌ 파싱 실패: 유효한 행이 없습니다.")
        return
    
    # 헤더 확인 (첫 행에 '종목코드' 또는 'code'가 있는지)
    first_row = rows[0]
    is_header = any('종목코드' in cell or 'code' in cell.lower() for cell in first_row)
    start_idx = 1 if is_header else 0
    if is_header:
        print("📋 첫 행을 헤더로 간주")
    
    # 첫 두 열 추출
    data = []
    for row in rows[start_idx:]:
        if len(row) >= 2:
            code = row[0].strip().strip('"')
            name = row[1].strip().strip('"')
            if code and name:
                code = code.zfill(6)
                if code.isdigit() and len(code) == 6:
                    data.append((code, name))
    
    if not data:
        print("❌ 유효한 종목 데이터가 없습니다.")
        return
    
    # 깨끗한 CSV로 저장
    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['code', 'name'])
        writer.writerows(data)
    
    print(f"✅ 변환 완료: {len(data)}개 종목 -> {output_file}")

if __name__ == "__main__":
    clean_krx_csv()