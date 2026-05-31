"""Preview 5 CCTV clips with YOLOv8-n bounding boxes."""
import cv2
import numpy as np
from pathlib import Path
from ultralytics import YOLO

# Camera mapping from the project setup
MAPPING = {
    "CAM_ENTRY_01": "CAM 3.mp4",
    "CAM_FLOOR_SKIN": "CAM 1.mp4",
    "CAM_FLOOR_MAKEUP": "CAM 2.mp4",
    "CAM_CASH_COUNTER": "CAM 5.mp4",
    "CAM_STOCKROOM": "CAM 4.mp4"
}

def main():
    cctv_dir = Path("data-provided/CCTV Footage")
    
    print("Loading YOLOv8 Nano model (much faster for preview!)...")
    model = YOLO("yolov8n.pt")
    
    caps = {}
    print("Opening video files...")
    for cam, clip in MAPPING.items():
        path = cctv_dir / clip
        if path.exists():
            print(f" -> Found {clip}")
            caps[cam] = cv2.VideoCapture(str(path))
        else:
            print(f" -> Missing {path}")
            
    if not caps:
        print("No videos found! Make sure data-provided/CCTV Footage exists.")
        return
        
    width, height = 480, 270 # Resize each box so it fits on screen
    
    print("Starting preview... (Note: Running 5 videos through AI will be slow and look like a slideshow!)")
    print("Press 'q' inside the video window to quit.")
    
    while True:
        frames = []
        cams = list(caps.keys())
        
        for cam in cams:
            cap = caps[cam]
            # Grab a frame. Skip some frames to make it look faster if it's lagging
            for _ in range(5):
                ret, frame = cap.read()
                if not ret:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
            
            # Predict (detect people) and draw bounding boxes using smaller imgsz for speed
            results = model.predict(frame, classes=[0], verbose=False, conf=0.20, imgsz=320)
            if results:
                # .plot() returns a numpy array with boxes drawn
                annotated = results[0].plot()
            else:
                annotated = frame
                
            # Resize and add the camera name text
            resized = cv2.resize(annotated, (width, height))
            cv2.putText(resized, cam, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            frames.append(resized)
            
        # Pad with black frames if we have less than 6 (so we can make a 3x2 grid)
        while len(frames) < 6:
            frames.append(np.zeros((height, width, 3), dtype=np.uint8))
            
        # Create a 3x2 grid
        row1 = np.hstack((frames[0], frames[1], frames[2]))
        row2 = np.hstack((frames[3], frames[4], frames[5]))
        grid = np.vstack((row1, row2))
        
        window_name = "Apex Retail - YOLO Live Preview (Press 'q' to quit)"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        # Force the window to appear on top of other windows
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
        cv2.imshow(window_name, grid)
        
        print("Rendered frame... (check your taskbar if you don't see the window!)")
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    for cap in caps.values():
        cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
