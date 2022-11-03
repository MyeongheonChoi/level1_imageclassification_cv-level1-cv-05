import argparse
import multiprocessing
import os
from importlib import import_module
import numpy as np

import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from dataset import TestDataset, MaskBaseDataset

from tqdm import tqdm


def load_model(saved_model, num_classes, device):
    model_cls = getattr(import_module("model"), args.model)
    model = model_cls(
        num_classes=num_classes
    )

    # tarpath = os.path.join(saved_model, 'best.tar.gz')
    # tar = tarfile.open(tarpath, 'r:gz')
    # tar.extractall(path=saved_model)

    model_path = os.path.join(saved_model, 'best.pth')
    model.load_state_dict(torch.load(model_path, map_location=device))

    return model


@torch.no_grad()
def inference(data_dir, model_dir, output_dir, args, usebbox):
    """
    """
    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    num_classes = MaskBaseDataset.num_classes  # 18
    model = load_model(model_dir, num_classes, device).to(device)
    model.eval()

    img_root = os.path.join(data_dir, 'images')
    info_path = os.path.join(data_dir, 'info.csv')
    info = pd.read_csv(info_path)
    bb_root = os.path.join(data_dir, 'boundingbox')

    img_paths = [os.path.join(img_root, img_id) for img_id in info.ImageID]
    bb_paths = [os.path.join(bb_root, img_id) for img_id in info.ImageID]

    dataset = TestDataset(img_paths, bb_paths, args.resize, usebbox)
    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=args.batch_size,
        num_workers=multiprocessing.cpu_count() // 2,
        shuffle=False,
        pin_memory=use_cuda,
        drop_last=False,
    )

    print("Calculating inference results..")
    preds = []
    softmax = nn.Softmax(dim=1)
    with torch.no_grad():
        for idx, images in enumerate(tqdm(loader)):
            images = images.to(device)
            pred = model(images)
            if args.voting_type == 'hard':
                pred = pred.argmax(dim=-1)
            elif args.voting_type == 'soft':
                pred = softmax(pred)
            preds.extend(pred.cpu().numpy())

    if args.voting_type == 'hard':
        info['ans'] = preds
    elif args.voting_type == 'soft':
        preds = np.array(preds)
        info = info.drop(columns=['ans'])
        info = pd.DataFrame(pd.np.column_stack([info, preds]))
        columns_name = ["ImageID"]
        for i in range(num_classes):
            columns_name.append(str(i))
        info.columns = columns_name

    save_path = os.path.join(output_dir, f'output.csv')
    info.to_csv(save_path, index=False)
    print(f"Inference Done! Inference result saved at {save_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    # Data and model checkpoints directories
    parser.add_argument('--batch_size', type=int, default=64,
                        help='input batch size for validing (default: 1000)')
    parser.add_argument('--resize', type=tuple, default=(384, 384),
                        help='resize size for image when you trained (default: (96, 128))')
    parser.add_argument('--model', type=str, default='SwinTransformerV2',
                        help='model type (default: BaseModel)')
    parser.add_argument('--usebbox', type=str, default='yes',
                        help='use bounding box (default: no (no, yes))')

    # Container environment
    parser.add_argument('--data_dir', type=str,
                        default=os.environ.get('SM_CHANNEL_EVAL', '/opt/ml/input/data/eval'))
    parser.add_argument('--model_dir', type=str, default=os.environ.get(
        'SM_CHANNEL_MODEL', '/opt/ml/model/Swin_Large_Weighted_Stratified_bbox_rem_58'))
    parser.add_argument('--output_dir', type=str,
                        default=os.environ.get('SM_OUTPUT_DATA_DIR', './output'))
    parser.add_argument('--voting_type', type=str, default='hard',
                        help='Output value type (soft or hard) (defalut: hard)')

    args = parser.parse_args()

    data_dir = args.data_dir
    model_dir = args.model_dir
    output_dir = args.output_dir
    usebbox = args.usebbox

    os.makedirs(output_dir, exist_ok=True)

    inference(data_dir, model_dir, output_dir, args, usebbox)
