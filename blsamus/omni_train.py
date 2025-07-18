import argparse
import os
import random
import numpy as np
import torch
import torch.backends.cudnn as cudnn
# 选择使用的模型
USE_SAMUS = True  # 设置为 True 使用 SAMUS，False 使用原始 ViT_omni

if USE_SAMUS:
    from networks.samus_adapter import SAMUSAdapter as ModelClass
else:
    from networks.omni_vision_transformer import OmniVisionTransformer as ModelClass

from omni_trainer import omni_train
from config import get_config

from omni_trainer import omni_train
from config import get_config



from omni_trainer import omni_train
from config import get_config

parser = argparse.ArgumentParser()
parser.add_argument('--root_path', type=str,
                    default='/root/autodl-tmp/wsp_uusic/blsamus/data', help='root dir for data')
parser.add_argument('--output_dir', type=str, default='/home/wtchen/wsp/wsp_uusic/blsamus/exp_out/samus_debug', help='output dir')
parser.add_argument('--max_epochs', type=int,
                    default=200, help='maximum epoch number to train')
parser.add_argument('--batch_size', type=int,
                    default=4, help='batch_size per gpu')
parser.add_argument('--gpu', type=str, default=None)
parser.add_argument('--deterministic', type=int,  default=1,
                    help='whether use deterministic training')
parser.add_argument('--base_lr', type=float,  default=0.01,
                    help='segmentation network learning rate')
parser.add_argument('--img_size', type=int,
                    default=224, help='input patch size of network input')
parser.add_argument('--seed', type=int,
                    default=1234, help='random seed')
parser.add_argument('--cfg', type=str, default="/root/autodl-tmp/wsp_uusic/blsamus/configs/samus_config.yaml",
                    metavar="FILE", help='path to config file', )
parser.add_argument(
    "--opts",
    help="Modify config options by adding 'KEY VALUE' pairs. ",
    default=None,
    nargs='+',
)
parser.add_argument('--zip', action='store_true', help='use zipped dataset instead of folder dataset')
parser.add_argument('--cache-mode', type=str, default='part', choices=['no', 'full', 'part'],
                    help='no: no cache, '
                    'full: cache all data, '
                    'part: sharding the dataset into non-overlapping pieces and only cache one piece')
parser.add_argument('--resume', help='resume from checkpoint')
parser.add_argument('--accumulation-steps', type=int, help="gradient accumulation steps")
parser.add_argument('--use-checkpoint', action='store_true',
                    help="whether to use gradient checkpointing to save memory")
parser.add_argument('--amp-opt-level', type=str, default='O1', choices=['O0', 'O1', 'O2'],
                    help='mixed precision opt level, if O0, no amp is used')
parser.add_argument('--tag', help='tag of experiment')
parser.add_argument('--eval', action='store_true', help='Perform evaluation only')
parser.add_argument('--throughput', action='store_true', help='Test throughput only')

parser.add_argument('--pretrain_ckpt', type=str, help='pretrained checkpoint')

parser.add_argument('--prompt', action='store_true', help='using prompt for training')
parser.add_argument('--adapter_ft', action='store_true', help='using adapter for fine-tuning')



args = parser.parse_args()

config = get_config(args)
args.num_classes = config.MODEL.NUM_CLASSES  # 确保这行存在
# args.batch_size = 2

if __name__ == "__main__":
    if not args.deterministic:
        cudnn.benchmark = True
        cudnn.deterministic = False
    else:
        cudnn.benchmark = False
        cudnn.deterministic = True

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)

    if args.batch_size != 24 and args.batch_size % 6 == 0:
        args.base_lr *= args.batch_size / 24

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)

    net = ModelClass(
        config,
        prompt=args.prompt,
    ).cuda()
    if args.pretrain_ckpt is not None:
        net.load_from_self(args.pretrain_ckpt)
    else:
        net.load_from(config)

    if args.prompt and args.adapter_ft:

        for name, param in net.named_parameters():
            if 'prompt' in name:
                param.requires_grad = True
                print(name)
            else:
                param.requires_grad = False
    
    omni_train(args, net, args.output_dir)