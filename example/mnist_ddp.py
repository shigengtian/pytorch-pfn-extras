import argparse
import os

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms

import pytorch_pfn_extras as ppe
import pytorch_pfn_extras.training.extensions as extensions


class Net(nn.Module):
    def __init__(self, lazy):
        super().__init__()
        if lazy:
            self.conv1 = ppe.nn.LazyConv2d(None, 20, 5, 1)
            self.conv2 = ppe.nn.LazyConv2d(None, 50, 5, 1)
            self.fc1 = ppe.nn.LazyLinear(None, 500)
            self.fc2 = ppe.nn.LazyLinear(None, 10)
        else:
            self.conv1 = nn.Conv2d(1, 20, 5, 1)
            self.conv2 = nn.Conv2d(20, 50, 5, 1)
            self.fc1 = nn.Linear(4 * 4 * 50, 500)
            self.fc2 = nn.Linear(500, 10)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.max_pool2d(x, 2, 2)
        x = F.relu(self.conv2(x))
        x = F.max_pool2d(x, 2, 2)
        x = x.flatten(start_dim=1)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return F.log_softmax(x, dim=1)


def train(manager, args, model, device, train_loader):
    while not manager.stop_trigger:
        model.train()
        for _, (data, target) in enumerate(train_loader):
            with manager.run_iteration(step_optimizers=['main']):
                data, target = data.to(device), target.to(device)
                output = model(data)
                loss = F.nll_loss(output, target)
                ppe.reporting.report({'train/loss': loss.item()})
                loss.backward()


def test(args, model, device, data, target):
    """ The extension loops over the iterator in order to
        drive the evaluator progress bar and reporting
        averages
    """
    model.eval()
    test_loss = 0
    correct = 0
    data, target = data.to(device), target.to(device)
    output = model(data)
    # Final result will be average of averages of the same size
    test_loss += F.nll_loss(output, target, reduction='mean').item()
    ppe.reporting.report({'val/loss': test_loss})
    pred = output.argmax(dim=1, keepdim=True)
    correct += pred.eq(target.view_as(pred)).sum().item()
    ppe.reporting.report({'val/acc': correct / len(data)})


def init_distributed(use_cuda=True):
    # setup env for torch.distributed
    comm_world_size = int(os.environ["OMPI_COMM_WORLD_SIZE"])
    comm_rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    comm_local_rank = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])

    os.environ["WORLD_SIZE"] = str(comm_world_size)
    os.environ["RANK"] = str(comm_rank)

    if comm_rank == 0:
        print("World size = {}".format(comm_world_size))
    print("Rank = {}, Local Rank = {}".format(comm_rank, comm_local_rank))

    torch.cuda.set_device(comm_local_rank)
    torch.distributed.init_process_group(backend='nccl', init_method='env://')

    device = torch.device(
        "cuda:{}".format(comm_local_rank) if use_cuda else "cpu")

    return comm_world_size, comm_rank, comm_local_rank, device


def main():
    # Training settings
    parser = argparse.ArgumentParser(description='PyTorch MNIST Example')
    parser.add_argument('--batch-size', type=int, default=64, metavar='N',
                        help='input batch size for training (default: 64)')
    parser.add_argument('--test-batch-size', type=int, default=1000,
                        metavar='N',
                        help='input batch size for testing (default: 1000)')
    parser.add_argument('--epochs', type=int, default=10, metavar='N',
                        help='number of epochs to train (default: 10)')
    parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                        help='learning rate (default: 0.01)')
    parser.add_argument('--momentum', type=float, default=0.5, metavar='M',
                        help='SGD momentum (default: 0.5)')
    parser.add_argument('--no-cuda', dest='cuda',
                        action='store_false', default=True,
                        help='disables CUDA training')
    parser.add_argument('--seed', type=int, default=1, metavar='S',
                        help='random seed (default: 1)')
    parser.add_argument('--save-model', action='store_true', default=False,
                        help='For Saving the current Model')
    parser.add_argument('--snapshot', type=str, default=None,
                        help='path to snapshot file')
    parser.add_argument('--no-lazy', dest='lazy',
                        action='store_false', default=True,
                        help='do not use lazy modules')
    args = parser.parse_args()
    use_cuda = args.cuda and torch.cuda.is_available()

    # torch.backends.cudnn.benchmark = False
    # torch.backends.cudnn.deterministic = True
    torch.manual_seed(args.seed)

    comm_world_size, comm_rank, comm_local_rank, device = init_distributed(
        use_cuda)
    if comm_rank == 0:
        print("World size = {}".format(comm_world_size))
    print("Rank = {}, Local Rank = {}".format(comm_rank, comm_local_rank))
    print("Device = {}".format(device))

    kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}
    dataset_root = '../data'
    if comm_local_rank == 0:
        # download mnist
        datasets.MNIST(dataset_root, download=True)
    torch.distributed.barrier()

    train_dataset = datasets.MNIST(
        dataset_root,
        train=True,
        transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,)),
        ]))
    test_dataset = datasets.MNIST(
        dataset_root,
        train=False,
        transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,)),
        ]))

    train_sampler = torch.utils.data.DistributedSampler(
        train_dataset, num_replicas=comm_world_size, rank=comm_rank)
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, sampler=train_sampler,
        **kwargs)

    test_dataset_indices = list(range(len(test_dataset)))
    local_test_dataset_indices = test_dataset_indices[
        comm_rank:len(test_dataset_indices):comm_world_size]
    local_test_dataset = torch.utils.data.Subset(
        test_dataset, local_test_dataset_indices)
    test_loader = torch.utils.data.DataLoader(
        local_test_dataset, batch_size=args.test_batch_size, shuffle=True,
        **kwargs)

    model = Net(args.lazy)
    model.to(device)
    if args.lazy:
        # You need to run a dummy forward to initialize parameters.
        # This should be done before passing parameter list to optimizers.
        dummy_input = train_loader.dataset[0][0].unsqueeze(0).to(device)
        model(dummy_input)

    model = ppe.nn.parallel.DistributedDataParallel(model)

    optimizer = optim.SGD(
        model.parameters(), lr=args.lr, momentum=args.momentum)

    # manager.extend(...) also works
    if comm_local_rank == 0:
        my_extensions = [
            extensions.LogReport(),
            extensions.ProgressBar(),
            extensions.observe_lr(optimizer=optimizer),
            extensions.ParameterStatistics(model, prefix='model'),
            extensions.VariableStatisticsPlot(model),
            extensions.Evaluator(
                test_loader, model,
                eval_func=lambda data, target:
                    test(args, model, device, data, target),
                progress_bar=True),
            extensions.PlotReport(
                ['train/loss', 'val/loss'], 'epoch', filename='loss.png'),
            extensions.PrintReport(['epoch', 'iteration',
                                    'train/loss', 'lr',
                                    'model/fc2.bias/grad/min',
                                    'val/loss', 'val/acc']),
            extensions.snapshot(),
        ]
    else:
        my_extensions = []

    # Custom stop triggers can be added to the manager and
    # their status accessed through `manager.stop_trigger`
    trigger = None
    # trigger = ppe.training.triggers.EarlyStoppingTrigger(
    #     check_trigger=(1, 'epoch'), monitor='val/loss')
    manager = ppe.training.ExtensionsManager(
        model, optimizer, args.epochs,
        extensions=my_extensions,
        iters_per_epoch=len(train_loader),
        stop_trigger=trigger)
    # Lets load the snapshot
    if args.snapshot is not None:
        state = torch.load(args.snapshot)
        manager.load_state_dict(state)
    train(manager, args, model, device, train_loader)
    # Test function is called from the evaluator extension
    # to get access to the reporter and other facilities
    # test(args, model, device, test_loader)

    if (args.save_model):
        torch.save(model.state_dict(), "mnist_cnn.pt")

    # Wait for all processes to finish to complete successfully
    torch.distributed.barrier()


if __name__ == '__main__':
    main()
