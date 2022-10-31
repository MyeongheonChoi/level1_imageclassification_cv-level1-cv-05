import glob
import json
import multiprocessing
import os
import random
import re
from importlib import import_module
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
from torchmetrics import F1Score
from torch.optim.lr_scheduler import StepLR
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter

# from dataset import MaskBaseDataset
from loss import create_criterion
from parse import parse_args

def seed_everything(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)  # if use multi-GPU
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    np.random.seed(seed)
    random.seed(seed)


def get_lr(optimizer):
    a=[]
    for param_group in optimizer.param_groups:
        a.append(param_group['lr'])
    return a[-1]

def mask_grid_image(np_images, gts, preds, n=16, shuffle=False):
    batch_size = np_images.shape[0]
    assert n <= batch_size

    choices = random.choices(range(batch_size), k=n) if shuffle else list(range(n))
    figure = plt.figure(figsize=(12, 18 + 2))  # cautions: hardcoded, 이미지 크기에 따라 figsize 를 조정해야 할 수 있습니다. T.T
    plt.subplots_adjust(top=0.8)  # cautions: hardcoded, 이미지 크기에 따라 top 를 조정해야 할 수 있습니다. T.T
    n_grid = int(np.ceil(n ** 0.5))

    for idx, choice in enumerate(choices):
        gt = gts[choice].item()
        pred = preds[choice].item()

        image = np_images[choice]
        title = f"mask - gt: {gt}, pred: {pred}"

        plt.subplot(n_grid, n_grid, idx + 1, title=title)
        plt.xticks([])
        plt.yticks([])
        plt.grid(False)
        plt.imshow(image, cmap=plt.cm.binary)

    return figure


def gender_age_grid_image(np_images, gts, preds, n=16, shuffle=False):
    batch_size = np_images.shape[0]
    assert n <= batch_size

    gender_gts, age_gts = gts
    gender_preds, age_preds = preds

    choices = random.choices(range(batch_size), k=n) if shuffle else list(range(n))
    figure = plt.figure(figsize=(12, 18 + 2))  # cautions: hardcoded, 이미지 크기에 따라 figsize 를 조정해야 할 수 있습니다. T.T
    plt.subplots_adjust(top=0.8)  # cautions: hardcoded, 이미지 크기에 따라 top 를 조정해야 할 수 있습니다. T.T
    n_grid = int(np.ceil(n ** 0.5))
    # task = ["gender", "age"]
    for idx, choice in enumerate(choices):
        gender_gt = gender_gts[choice].item()
        age_gt = age_gts[choice].item()
        gender_pred = gender_preds[choice].item()
        age_pred = age_preds[choice].item()

        image = np_images[choice]
        # gt_decoded_labels = MaskBaseDataset.decode_multi_class(gt)
        # pred_decoded_labels = MaskBaseDataset.decode_multi_class(pred)
        title = f"gender - gt: {gender_gt}, pred: {gender_pred:4.4}\nage - gt: {age_gt}, pred: {age_pred:4.4}"
        # title = "\n".join([
        #     f"{task} - gt: {gt_label}, pred: {pred_label}"
        #     for gt_label, pred_label, task
        #     in zip(gt_decoded_labels, pred_decoded_labels, tasks)
        # ])

        plt.subplot(n_grid, n_grid, idx + 1, title=title)
        plt.xticks([])
        plt.yticks([])
        plt.grid(False)
        plt.imshow(image, cmap=plt.cm.binary)

    return figure


def increment_path(path, exist_ok=False):
    """ Automatically increment path, i.e. runs/exp --> runs/exp0, runs/exp1 etc.

    Args:
        path (str or pathlib.Path): f"{model_dir}/{args.name}".
        exist_ok (bool): whether increment path (increment if False).
    """
    path = Path(path)
    if (path.exists() and exist_ok) or (not path.exists()):
        return str(path)
    else:
        dirs = glob.glob(f"{path}*")
        matches = [re.search(rf"%s(\d+)" % path.stem, d) for d in dirs]
        i = [int(m.groups()[0]) for m in matches if m]
        n = max(i) + 1 if i else 2
        return f"{path}{n}"


def mask_train(data_dir, model_dir, args):
    seed_everything(args.seed)

    save_dir = increment_path(os.path.join(model_dir, args.name))

    # -- settings
    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    # -- dataset
    dataset_module = getattr(import_module("dataset"), args.dataset)  # default: MaskBaseDataset
    dataset = dataset_module(
        data_dir=data_dir,
    )
    num_classes = dataset.num_classes  # 3

    # -- augmentation
    transform_module = getattr(import_module("dataset"), args.augmentation)  # default: BaseAugmentation
    transform = transform_module(
        resize=args.resize,
        mean=dataset.mean,
        std=dataset.std,
    )
    dataset.set_transform(transform)

    # -- data_loader
    train_set, val_set = dataset.split_dataset()

    train_loader = DataLoader(
        train_set,
        batch_size=args.batch_size,
        num_workers=multiprocessing.cpu_count() // 2,
        shuffle=True,
        pin_memory=use_cuda,
        drop_last=True,
    )

    val_loader = DataLoader(
        val_set,
        batch_size=args.valid_batch_size,
        num_workers=multiprocessing.cpu_count() // 2,
        shuffle=False,
        pin_memory=use_cuda,
        drop_last=True,
    )

    # -- model
    model_module = getattr(import_module("model"), args.model)  # default: BaseModel
    model = model_module().to(device)
    model = torch.nn.DataParallel(model)

    # -- loss & metric
    criterion = create_criterion(args.criterion)  # default: cross_entropy
    opt_module = getattr(import_module("torch.optim"), args.optimizer)  # default: SGD
    optimizer = opt_module(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr,
        weight_decay=5e-4
    )
    scheduler = StepLR(optimizer, args.lr_decay_step, gamma=0.5)

    # -- logging
    logger = SummaryWriter(log_dir=save_dir)
    with open(os.path.join(save_dir, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump(vars(args), f, ensure_ascii=False, indent=4)

    best_val_acc = 0
    best_val_loss = np.inf
    for epoch in range(args.epochs):
        # train loop
        model.train()
        loss_value = 0
        matches = 0
        for idx, train_batch in enumerate(train_loader):
            inputs, labels = train_batch
            inputs = inputs.to(device)
            labels = labels.to(device)

            optimizer.zero_grad()

            outs = model(inputs)
            preds = torch.argmax(outs, dim=-1)
            loss = criterion(outs.cpu(), labels.cpu())

            loss.backward()
            optimizer.step()

            loss_value += loss.item()
            matches += (preds == labels).sum().item()
            if (idx + 1) % args.log_interval == 0:
                train_loss = loss_value / args.log_interval
                train_acc = matches / args.batch_size / args.log_interval
                current_lr = get_lr(optimizer)
                print(
                    f"Epoch[{epoch}/{args.epochs}]({idx + 1}/{len(train_loader)}) || "
                    f"training loss {train_loss:4.4} || training accuracy {train_acc:4.2%} || lr {current_lr}"
                )
                logger.add_scalar("Train/loss", train_loss, epoch * len(train_loader) + idx)
                logger.add_scalar("Train/accuracy", train_acc, epoch * len(train_loader) + idx)

                loss_value = 0
                matches = 0

        scheduler.step()

        # val loop
        with torch.no_grad():
            print("Calculating validation results...")
            model.eval()
            val_loss_items = []
            val_acc_items = []
            figure = None
            for val_batch in val_loader:
                inputs, labels = val_batch
                inputs = inputs.to(device)
                labels = labels.to(device)

                outs = model(inputs)
                preds = torch.argmax(outs, dim=-1)

                loss_item = criterion(outs.cpu(), labels.cpu()).item()
                acc_item = (labels == preds).sum().item()
                val_loss_items.append(loss_item)
                val_acc_items.append(acc_item)

                if figure is None:
                    inputs_np = torch.clone(inputs).detach().cpu().permute(0, 2, 3, 1).numpy()
                    inputs_np = dataset_module.denormalize_image(inputs_np, dataset.mean, dataset.std)
                    figure = mask_grid_image(
                        inputs_np, labels, preds, n=16, shuffle=args.dataset != "MaskSplitByProfileDataset"
                    )

            val_loss = np.sum(val_loss_items) / len(val_loader)
            val_acc = np.sum(val_acc_items) / len(val_set)
            best_val_loss = min(best_val_loss, val_loss)
            if val_acc > best_val_acc:
                print(f"New best model for val accuracy : {val_acc:4.2%}! saving the best model..")
                torch.save(model.module.state_dict(), f"{save_dir}/best.pth")
                best_val_acc = val_acc
            torch.save(model.module.state_dict(), f"{save_dir}/last.pth")
            print(
                f"[Val] acc : {val_acc:4.2%}, loss: {val_loss:4.2} || "
                f"best acc : {best_val_acc:4.2%}, best loss: {best_val_loss:4.2}"
            )
            logger.add_scalar("Val/loss", val_loss, epoch)
            logger.add_scalar("Val/accuracy", val_acc, epoch)
            logger.add_figure("results", figure, epoch)
            print()


def gender_age_train(data_dir, model_dir, args):
    seed_everything(args.seed)

    save_dir = increment_path(os.path.join(model_dir, args.name))

    # -- settings
    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    # -- dataset
    dataset_module = getattr(import_module("dataset"), args.dataset)  # default: MaskBaseDataset
    dataset = dataset_module(
        data_dir=data_dir,
    )

    # -- augmentation
    transform_module = getattr(import_module("dataset"), args.augmentation)  # default: BaseAugmentation
    transform = transform_module(
        resize=args.resize,
        mean=dataset.mean,
        std=dataset.std,
    )
    dataset.set_transform(transform)

    # -- data_loader
    train_set, val_set = dataset.split_dataset()

    train_loader = DataLoader(
        train_set,
        batch_size=args.batch_size,
        num_workers=multiprocessing.cpu_count() // 2,
        shuffle=True,
        pin_memory=use_cuda,
        drop_last=True,
    )

    val_loader = DataLoader(
        val_set,
        batch_size=args.valid_batch_size,
        num_workers=multiprocessing.cpu_count() // 2,
        shuffle=False,
        pin_memory=use_cuda,
        drop_last=True,
    )

    # -- model
    model_module = getattr(import_module("model"), args.model)  # default: BaseModel
    model = model_module().to(device)
    model = torch.nn.DataParallel(model)

    # -- loss & metric
    gender_criterion = torch.nn.BCEWithLogitsLoss()
    age_criterion = create_criterion(args.criterion)  # default: cross_entropy

    opt_module = getattr(import_module("torch.optim"), args.optimizer)  # default: SGD

    gender_optimizer = opt_module([
        {"params" : model.module.res.parameters(),'lr' : 1e-4},
        {"params" : model.module.fc_gender.parameters()}],
        lr=args.lr,weight_decay=5e-4)

    age_optimizer = opt_module([
        {"params" : model.module.res.parameters(),'lr' : 1e-4},
        {"params" : model.module.fc_age.parameters()}],
        lr=args.lr,weight_decay=5e-4)
    
    # optimizer = opt_module(
    #     filter(lambda p: p.requires_grad, model.parameters()),
    #     lr=args.lr,
    #     weight_decay=5e-4
    # )
    gender_scheduler = StepLR(gender_optimizer, args.lr_decay_step, gamma=0.5)
    age_scheduler = StepLR(age_optimizer, args.lr_decay_step, gamma=0.5)

    # -- logging
    logger = SummaryWriter(log_dir=save_dir)
    with open(os.path.join(save_dir, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump(vars(args), f, ensure_ascii=False, indent=4)

    best_val_gender_acc = 0
    best_val_loss = np.inf
    for epoch in range(args.epochs):
        # train loop
        model.train()
        gender_loss_value = 0
        age_loss_value = 0
        gender_matches = 0
        for idx, train_batch in enumerate(train_loader):
            inputs, gender_labels, age_labels = train_batch
            inputs = inputs.to(device)
            gender_labels = gender_labels.to(device)
            age_labels = age_labels.to(device)

            gender_optimizer.zero_grad()
            age_optimizer.zero_grad()

            gender_outs, age_outs = model(inputs)
            gender_outs = gender_outs.squeeze()
            gender_preds = torch.round(torch.nn.Sigmoid()(gender_outs))
            age_outs = age_outs.squeeze()

            gender_loss = gender_criterion(gender_outs, gender_labels.to(torch.float32))
            age_loss = age_criterion(age_outs, age_labels.to(torch.float32))

            gender_loss.backward(retain_graph=True)
            age_loss.backward()
            gender_optimizer.step()
            age_optimizer.step()

            gender_loss_value += gender_loss.item()
            age_loss_value += age_loss.item()
            gender_matches += (gender_preds == gender_labels).sum().item()
            #interval print
            if (idx + 1) % args.log_interval == 0:
                train_gender_loss = gender_loss_value / args.log_interval
                train_age_loss = age_loss_value / args.log_interval

                train_gender_acc = gender_matches / args.batch_size / args.log_interval

                current_gender_lr = get_lr(gender_optimizer)
                current_age_lr = get_lr(age_optimizer)
                print(
                    f"Epoch[{epoch}/{args.epochs}]({idx + 1}/{len(train_loader)}) || "
                    f"training gender accuracy {train_gender_acc:4.2%} || training gender loss {train_gender_loss:4.4} || training age loss {train_age_loss:4.4} || gender / age lr {current_gender_lr} / {current_age_lr}"
                )
                logger.add_scalar("Train/gender accuracy", train_gender_acc, epoch * len(train_loader) + idx)
                logger.add_scalar("Train/gender loss", train_gender_loss, epoch * len(train_loader) + idx)
                logger.add_scalar("Train/age loss", train_age_loss, epoch * len(train_loader) + idx)

                gender_matches = 0
                gender_loss_value = 0
                age_loss_value = 0
        
        gender_scheduler.step()
        age_scheduler.step()

        # val loop
        with torch.no_grad():
            print("Calculating validation results...")
            model.eval()
            val_gender_acc_items = []
            val_gender_loss_items = []
            val_age_loss_items = []
            figure = None
            for val_batch in val_loader:
                inputs, gender_labels, age_labels = val_batch
                inputs = inputs.to(device)
                gender_labels = gender_labels.to(device)
                age_labels = age_labels.to(device)

                gender_outs, age_outs = model(inputs)
                gender_outs = gender_outs.squeeze()
                gender_preds = torch.round(torch.nn.Sigmoid()(gender_outs))
                age_outs = age_outs.squeeze()

                gender_loss_item = gender_criterion(gender_outs, gender_labels.to(torch.float32)).item()
                age_loss_item = age_criterion(age_outs, age_labels.to(torch.float32)).item()

                gender_acc_item = (gender_preds == gender_labels).sum().item()

                val_gender_acc_items.append(gender_acc_item)
                val_gender_loss_items.append(gender_loss_item)
                val_age_loss_items.append(age_loss_item)

                if figure is None:
                    inputs_np = torch.clone(inputs).detach().cpu().permute(0, 2, 3, 1).numpy()
                    inputs_np = dataset_module.denormalize_image(inputs_np, dataset.mean, dataset.std)
                    figure = gender_age_grid_image(
                        inputs_np, (gender_labels, age_labels), (gender_preds, age_outs), n=16, shuffle=args.dataset != "MaskSplitByProfileDataset")

            val_gender_acc = np.sum(val_gender_acc_items) / len(val_set)
            val_gender_loss = np.sum(val_gender_loss_items) / len(val_loader)
            val_age_loss = np.sum(val_age_loss_items) / len(val_loader)

            best_val_gender_acc = max(best_val_gender_acc, val_gender_acc)
            val_loss = val_gender_loss + val_age_loss
            if val_loss < best_val_loss:
                print(f"New best model for val loss : {val_loss:4.4}! saving the best model..")
                torch.save(model.module.state_dict(), f"{save_dir}/best.pth")
                best_val_loss = val_loss
            torch.save(model.module.state_dict(), f"{save_dir}/last.pth")
            print(
                f"[Val] gender accuracy: {val_gender_acc:4.2%} || best gender accuracy: {best_val_gender_acc:4.2%}\n"
                f"gender loss: {val_gender_loss:4.4} || age loss: {val_age_loss:4.4} || best gender accuracy: {best_val_gender_acc:4.2%} || loss: {val_loss:4.4} || best loss: {best_val_loss:4.4}"
            )
            logger.add_scalar("Val/gender accuracy", val_gender_acc, epoch)
            logger.add_scalar("Val/gender loss", val_gender_loss, epoch)
            logger.add_scalar("Val/age loss", val_age_loss, epoch)
            logger.add_figure("results", figure, epoch)
            print()

  
if __name__ == '__main__':
    args = parse_args()
    print(args)

    data_dir = args.data_dir
    model_dir = args.model_dir

    mask_train(data_dir, model_dir, args)