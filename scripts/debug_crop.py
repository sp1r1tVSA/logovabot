import glob
from PIL import Image

def get_exact_row_y_centers(img: Image.Image):
    """
    Find 8 emblem row y-centers by scanning vertical brightness profile.
    """
    width, height = img.size
    # Scan column x=25 from y=50 to y=550
    brightness = []
    for y in range(50, height - 10):
        # Average brightness around x=25
        r, g, b = img.getpixel((25, y))[:3]
        val = (r + g + b) / 3.0
        brightness.append((y, val))
        
    # Find local peaks of brightness
    peaks = []
    in_peak = False
    cur_y_list = []
    
    for y, val in brightness:
        if val > 40: # non-background pixel
            in_peak = True
            cur_y_list.append(y)
        else:
            if in_peak:
                if len(cur_y_list) >= 10:
                    mid_y = int(sum(cur_y_list) / len(cur_y_list))
                    peaks.append(mid_y)
                in_peak = False
                cur_y_list = []
                
    if in_peak and len(cur_y_list) >= 10:
        mid_y = int(sum(cur_y_list) / len(cur_y_list))
        peaks.append(mid_y)
        
    return peaks

img_path = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры\Снимок экрана 2026-07-22 163104.png" # Round 2
img = Image.open(img_path)
peaks = get_exact_row_y_centers(img)
print("Detected emblem y-centers in Round 2:", peaks, "Total:", len(peaks))
