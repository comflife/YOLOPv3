import argparse
import time
from pathlib import Path
import cv2
import torch
import numpy as np

# Conclude setting / general reprocessing / plots / metrices / datasets
from utils.utils import \
    time_synchronized, select_device, increment_path, safe_polyfit,draw_grid,\
    scale_coords, xyxy2xywh, non_max_suppression, split_for_trace_model, show_lane_lines,\
    driving_area_mask, lane_line_mask, plot_one_box, show_seg_result, detect_lane_with_sliding_window,\
    AverageMeter, \
    LoadImages


def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', nargs='+', type=str, default='data/weights/yolopv2.pt', help='model.pt path(s)')
    parser.add_argument('--source', type=str, default='data/example.jpg', help='source')  # file/folder, 0 for webcam
    parser.add_argument('--img-size', type=int, default=256, help='inference size (pixels)')
    parser.add_argument('--conf-thres', type=float, default=0.3, help='object confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.45, help='IOU threshold for NMS')
    parser.add_argument('--device', default='0', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --class 0, or --class 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--project', default='runs/detect', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    return parser

# Bird Eye View Transformation
def bird_eye_view_transform(img, M):
    return cv2.warpPerspective(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR)

# Check if the mask is empty
def is_mask_empty(mask):
    return not np.any(mask)

def detect():
    # setting and directories
    source, weights, save_txt, imgsz = opt.source, opt.weights, opt.save_txt, opt.img_size

    inf_time = AverageMeter()
    waste_time = AverageMeter()
    nms_time = AverageMeter()

    # Load model
    stride = 32
    model = torch.jit.load(weights)
    device = select_device(opt.device)
    half = device.type != 'cpu'  # half precision only supported on CUDA
    model = model.to(device)
    if half:
        model.half()  # to FP16
    model.eval()

    # Set Dataloader
    vid_path, vid_writer = None, None
    if opt.source == "0":  # 카메라 입력 체크
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise IOError("Cannot open webcam")
    else:
        dataset = LoadImages(opt.source, img_size=imgsz, stride=stride)

    if device.type != 'cpu':
        model(torch.zeros(1, 3, imgsz, imgsz).to(device).type_as(next(model.parameters())))  # run once

    t0 = time.time()

    # Source points - define a trapezoid
    src = np.float32([[40, 170],  # Bottom left
                    [188, 170],  # Bottom right
                    [168, 127],  # Top right
                    [60, 127]])  # Top left

    # Destination points - define a rectangle
    dst = np.float32([[0, imgsz],  # Bottom left
                    [imgsz, imgsz],  # Bottom right
                    [imgsz, 0],  # Top right
                    [0, 0]])  # Top left

    # Compute the perspective transform matrix
    M = cv2.getPerspectiveTransform(src, dst)


    # Bird eye view transformation matrix

    def inference(img, im0, path):
        # Inference code
        t1 = time_synchronized()
        
        [pred, anchor_grid], seg, ll = model(img)
        t2 = time_synchronized()
        tw1 = time_synchronized()
        pred = split_for_trace_model(pred, anchor_grid)
        tw2 = time_synchronized()
        t3 = time_synchronized()
        pred = non_max_suppression(pred, opt.conf_thres, opt.iou_thres, classes=opt.classes,
                                   agnostic=opt.agnostic_nms)
        t4 = time_synchronized()

        da_seg_mask = driving_area_mask(seg, grid_size=10, grid_range=(4,6,2,4))
        ll_seg_mask = lane_line_mask(ll, grid_size=10, grid_range=(3,6,1,5))
        stop_seg_mask = lane_line_mask(ll, grid_size=10, grid_range=(0,3,2,4))

        # Check if the masks are empty
        # print('da_seg_mask is empty:', is_mask_empty(da_seg_mask))
        print('ll_seg_mask is empty:', is_mask_empty(ll_seg_mask))
        print('stop_seg_mask is empty:', is_mask_empty(stop_seg_mask))

        # Process detections
        for i, det in enumerate(pred):
            p = Path(path)
            if opt.source != "0":
                frame = getattr(dataset, 'frame', 0)

            s = '%gx%g ' % img.shape[2:]
            gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]

            if len(det):
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()

                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()

                for *xyxy, conf, cls in reversed(det):
                    if save_txt:
                        xywh = (xyxy2xywh(torch.tensor(xyxy).view(1, 4)) / gn).view(-1).tolist()
                        line = (cls, *xywh, conf) if opt.save_conf else (cls, *xywh)

            print(f'{s}Done. ({t2 - t1:.3f}s)')
            show_seg_result(im0, (da_seg_mask, ll_seg_mask, stop_seg_mask), img_shape=im0.shape[:2], is_demo=True)

            # Bird eye view transformation
            im0 = bird_eye_view_transform(im0, M)
            
            img_with_grid = draw_grid(im0, grid_size=10)

            cv2.imshow('Detection', img_with_grid)
            if cv2.waitKey(1) == ord('q'):
                if cap:
                    cap.release()
                cv2.destroyAllWindows()
                exit()

        inf_time.update(t2 - t1, img.size(0))
        nms_time.update(t4 - t3, img.size(0))
        waste_time.update(tw2 - tw1, img.size(0))

    if opt.source == "0":
        while True:
            path = "webcam_frame"
            ret, im0 = cap.read()
            if not ret:
                break

            img = cv2.resize(im0, (imgsz, imgsz))
            img = torch.from_numpy(img).permute(2, 0, 1).float().div(255.0).unsqueeze(0).to(device).type_as(
                next(model.parameters()))
            inference(img, im0, path)
    else:
        for path, img, im0s, vid_cap in dataset:
            inference(img, im0s, path)

    print('inf : (%.4fs/frame)   nms : (%.4fs/frame)' % (inf_time.avg, nms_time.avg))
    print(f'Done. ({time.time() - t0:.3f}s)')


if __name__ == '__main__':
    opt = make_parser().parse_args()
    print(opt)

    with torch.no_grad():
        detect()