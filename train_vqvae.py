import argparse

import os
import glob

from IPython.display import clear_output

import torch
from torch import nn, optim
from torch.utils.data import DataLoader

from torchvision import datasets, transforms, utils

from tqdm.autonotebook import tqdm

from vqvae import VQVAE
from scheduler import CycleScheduler


def train(epoch, loader, model, optimizer, scheduler, device, save_path=""):
    loader = tqdm(loader)

    criterion = nn.MSELoss()

    latent_loss_weight = 0.25
    sample_size = 25

    mse_sum = 0
    mse_n = 0

    for i, (img, label) in enumerate(loader):
        model.zero_grad()

        img = img.to(device)

        out, latent_loss = model(img)
        recon_loss = criterion(out, img)
        latent_loss = latent_loss.mean()
        loss = recon_loss + latent_loss_weight * latent_loss
        loss.backward()

        if scheduler is not None:
            scheduler.step()
        optimizer.step()

        mse_sum += recon_loss.item() * img.shape[0]
        mse_n += img.shape[0]

        lr = optimizer.param_groups[0]["lr"]

        loader.set_description(
            (
                f"epoch: {epoch + 1}; mse: {recon_loss.item():.5f}; "
                f"latent: {latent_loss.item():.3f}; avg mse: {mse_sum / mse_n:.5f}; "
                f"lr: {lr:.5f}"
            )
        )

        if i % 100 == 0:
            model.eval()

            sample = img[:sample_size]

            with torch.no_grad():
                out, _ = model(sample)

            utils.save_image(
                torch.cat([sample, out], 0),
                f"{save_path}/sample/{str(epoch + 1).zfill(5)}_{str(i).zfill(5)}.png",
                nrow=sample_size,
                normalize=True,
                range=(-1, 1),
            )

            model.train()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--epoch", type=int, default=560)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--sched", type=str)
    parser.add_argument("path", type=str)
    parser.add_argument("--save_path", type=str, default=".")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--resume", action="store_true", default=False)

    args = parser.parse_args()

    print(args)

    device = "cuda"

    transform = transforms.Compose(
        [
            transforms.Resize(args.size),
            transforms.CenterCrop(args.size),
            transforms.ToTensor(),
            transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
        ]
    )

    dataset = datasets.ImageFolder(args.path, transform=transform)
    loader = DataLoader(dataset, batch_size=128, shuffle=True, num_workers=4)

    model = nn.DataParallel(VQVAE()).to(device)

    if args.resume:
        checkpoints = glob.glob(f"{args.save_path}/checkpoint/vqvae_*.pt")
        checkpoints.sort(key=os.path.getmtime, reverse=True)
        args.start = int(checkpoints[0][-6:-3])
    
    if args.start > 0:
        checkpoint = f"{args.save_path}/checkpoint/vqvae_{str(args.start).zfill(3)}.pt"
        print(f"Resuming from {checkpoint}")
        model.module.load_state_dict(torch.load(checkpoint))
    else:
        print(f"Restarting training from beginning...")

    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    scheduler = None
    if args.sched == "cycle":
        scheduler = CycleScheduler(
            optimizer, args.lr, n_iter=len(loader) * args.epoch, momentum=None
        )

    for i in range(args.start, args.epoch):
        train(i, loader, model, optimizer, scheduler, device, args.save_path)
        torch.save(
            model.module.state_dict(),
            f"{args.save_path}/checkpoint/vqvae_{str(i + 1).zfill(3)}.pt",
        )
        clear_output()
