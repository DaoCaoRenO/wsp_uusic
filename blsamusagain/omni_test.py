import argparse
import logging
import os
import random
import sys
import numpy as np
import torch
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from tqdm import tqdm

from config import get_config

from datasets.dataset import CenterCropGenerator
from datasets.dataset import USdatasetCls, USdatasetSeg

from utils import omni_seg_test
from sklearn.metrics import accuracy_score

from networks.omni_vision_transformer import OmniVisionTransformer as ViT_omni

# 添加网络模块路径
current_dir = os.path.dirname(__file__)
networks_path = os.path.join(current_dir, 'networks')
if networks_path not in sys.path:
    sys.path.insert(0, networks_path)

# 添加 SAMUS 模型路径
samus_path = os.path.join(current_dir, '../KTD/SAMUS-main')
if samus_path not in sys.path:
    sys.path.insert(0, samus_path)

# 导入 SAMUSAdapter 类
try:
    from samus_adapter import SAMUSAdapter
    print("✓ SAMUSAdapter 导入成功")
    USE_SAMUS = True
except ImportError as e:
    print(f"✗ SAMUSAdapter 导入失败: {e}")
    USE_SAMUS = False

parser = argparse.ArgumentParser()
parser.add_argument('--root_path', type=str,
                    default='data/', help='root dir for data')
parser.add_argument('--output_dir', type=str, help='output dir')
parser.add_argument('--max_epochs', type=int, default=200, help='maximum epoch number to train')
parser.add_argument('--batch_size', type=int, default=16,
                    help='batch_size per gpu')
parser.add_argument('--img_size', type=int, default=224, help='input patch size of network input')
parser.add_argument('--is_saveout', action="store_true", help='whether to save results during inference')
parser.add_argument('--test_save_dir', type=str, default='../predictions', help='saving prediction as nii!')
parser.add_argument('--deterministic', type=int,  default=1, help='whether use deterministic training')
parser.add_argument('--base_lr', type=float,  default=0.01, help='segmentation network learning rate')
parser.add_argument('--seed', type=int, default=1234, help='random seed')
parser.add_argument('--cfg', type=str, default="configs/swin_tiny_patch4_window7_224_lite.yaml",
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
                    'part: sharding the dataset into nonoverlapping pieces and only cache one piece')
parser.add_argument('--resume', help='resume from checkpoint')
parser.add_argument('--accumulation-steps', type=int, help="gradient accumulation steps")
parser.add_argument('--use-checkpoint', action='store_true',
                    help="whether to use gradient checkpointing to save memory")
parser.add_argument('--amp-opt-level', type=str, default='O1', choices=['O0', 'O1', 'O2'],
                    help='mixed precision opt level, if O0, no amp is used')
parser.add_argument('--tag', help='tag of experiment')
parser.add_argument('--eval', action='store_true', help='Perform evaluation only')
parser.add_argument('--throughput', action='store_true', help='Test throughput only')

parser.add_argument('--prompt', action='store_true', help='using prompt')
parser.add_argument('--use_samus', action='store_true', help='use SAMUS model for testing')


args = parser.parse_args()
config = get_config(args)


def inference(args, model, test_save_path=None):
    import csv
    import time

    if not os.path.exists("exp_out/result.csv"):
        with open("exp_out/result.csv", 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['dataset', 'task', 'metric', 'time'])

    seg_test_set = [
        "BUS-BRA",
        "BUSIS",
        "BUSI",
        "CAMUS",
        "DDTI",
        "Fetal_HC",
        "KidneyUS",
        "private_Thyroid",
        "private_Kidney",
        "private_Fetal_Head",
        "private_Cardiac",
        "private_Breast_luminal",
        "private_Breast",
    ]

    for dataset_name in seg_test_set:
        num_classes = 2
        db_test = USdatasetSeg(
            base_dir=os.path.join(args.root_path, "segmentation", dataset_name),
            split="test",
            list_dir=os.path.join(args.root_path, "segmentation", dataset_name),
            transform=CenterCropGenerator(output_size=[args.img_size, args.img_size]),
            prompt=args.prompt
        )
        testloader = DataLoader(db_test, batch_size=1, shuffle=False, num_workers=1)
        logging.info("{} test iterations per epoch".format(len(testloader)))
        model.eval()

        metric_list = 0.0
        count_matrix = np.ones((len(db_test), num_classes-1))
        for i_batch, sampled_batch in tqdm(enumerate(testloader)):
            image, label, case_name = sampled_batch["image"], sampled_batch["label"], sampled_batch['case_name'][0]
            if args.prompt:
                position_prompt = torch.tensor(np.array(sampled_batch['position_prompt'])).permute([1, 0]).float()
                task_prompt = torch.tensor(np.array([[1], [0]])).permute([1, 0]).float()
                type_prompt = torch.tensor(np.array(sampled_batch['type_prompt'])).permute([1, 0]).float()
                nature_prompt = torch.tensor(np.array(sampled_batch['nature_prompt'])).permute([1, 0]).float()
                metric_i = omni_seg_test(image, label, model,
                                         classes=num_classes,
                                         test_save_path=test_save_path,
                                         case=case_name,
                                         prompt=args.prompt,
                                         type_prompt=type_prompt,
                                         nature_prompt=nature_prompt,
                                         position_prompt=position_prompt,
                                         task_prompt=task_prompt
                                         )
            else:
                metric_i = omni_seg_test(image, label, model,
                                         classes=num_classes,
                                         test_save_path=test_save_path,
                                         case=case_name)
            zero_label_flag = False
            for i in range(1, num_classes):
                if not metric_i[i-1][1]:
                    count_matrix[i_batch, i-1] = 0
                    zero_label_flag = True
            metric_i = [element[0] for element in metric_i]
            metric_list += np.array(metric_i)
            logging.info('idx %d case %s mean_dice %f' %
                         (i_batch, case_name, np.mean(metric_i, axis=0)))
            logging.info("This case has zero label: %s" % zero_label_flag)

        metric_list = metric_list / (count_matrix.sum(axis=0) + 1e-6)
        for i in range(1, num_classes):
            logging.info('Mean class %d mean_dice %f' % (i, metric_list[i-1]))
        performance = np.mean(metric_list, axis=0)
        logging.info('Testing performance in best val model: mean_dice : %f' % (performance))

        with open("exp_out/result.csv", 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if args.prompt:
                writer.writerow([dataset_name, 'omni_seg_prompt@'+args.output_dir, performance,
                                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())])
            else:
                writer.writerow([dataset_name, 'omni_seg@'+args.output_dir, performance,
                                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())])

    cls_test_set = [
        "Appendix",
        "BUS-BRA",
        "BUSI",
        "Fatty-Liver",
        "private_Liver",
        "private_Breast_luminal",
        "private_Breast",
        "private_Appendix",
    ]

    for dataset_name in cls_test_set:
        if dataset_name == "private_Breast_luminal":
            num_classes = 4
        else:
            num_classes = 2
        db_test = USdatasetCls(
            base_dir=os.path.join(args.root_path, "classification", dataset_name),
            split="test",
            list_dir=os.path.join(args.root_path, "classification", dataset_name),
            transform=CenterCropGenerator(output_size=[args.img_size, args.img_size]),
            prompt=args.prompt
        )

        testloader = DataLoader(db_test, batch_size=1, shuffle=False, num_workers=1)
        logging.info("{} test iterations per epoch".format(len(testloader)))
        model.eval()

        label_list = []
        prediction_list = []
        for i_batch, sampled_batch in tqdm(enumerate(testloader)):
            image, label, case_name = sampled_batch["image"], sampled_batch["label"], sampled_batch['case_name'][0]
            if args.prompt:
                position_prompt = torch.tensor(np.array(sampled_batch['position_prompt'])).permute([1, 0]).float()
                task_prompt = torch.tensor(np.array([[0], [1]])).permute([1, 0]).float()
                type_prompt = torch.tensor(np.array(sampled_batch['type_prompt'])).permute([1, 0]).float()
                nature_prompt = torch.tensor(np.array(sampled_batch['nature_prompt'])).permute([1, 0]).float()
                with torch.no_grad():
                    output = model((image.cuda(), position_prompt.cuda(), task_prompt.cuda(),
                                   type_prompt.cuda(), nature_prompt.cuda()))
            else:
                with torch.no_grad():
                    output = model(image.cuda())

            if num_classes == 4:
                logits = output[2]
            else:
                logits = output[1]

            prediction = np.argmax(torch.softmax(logits, dim=1).data.cpu().numpy())
            logging.info('idx %d case %s label: %d predict: %d' % (i_batch, case_name, label, prediction))

            label_list.append(label.numpy())
            prediction_list.append(prediction)

        label_list = np.array(label_list)
        prediction_list = np.array(prediction_list)
        for i in range(num_classes):
            logging.info('class %d acc %f' % (i, accuracy_score(
                (label_list == i).astype(int), (prediction_list == i).astype(int))))
        performance = accuracy_score(label_list, prediction_list)
        logging.info('Testing performance in best val model: acc : %f' % (performance))

        with open("exp_out/result.csv", 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if args.prompt:
                writer.writerow([dataset_name, 'omni_cls_prompt@'+args.output_dir, performance,
                                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())])
            else:
                writer.writerow([dataset_name, 'omni_cls@'+args.output_dir, performance,
                                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())])


# 修改第241行附近的模型创建代码
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

    # 根据训练时使用的模型创建相应的测试模型
    # if USE_SAMUS:
    #     print("使用 SAMUS 模型进行测试")
    #     net = SAMUSAdapter(
    #         config,
    #         prompt=args.prompt,
    #     ).cuda()
    # else:
    #     print("使用 ViT_omni 模型进行测试")
    #     net = ViT_omni(
    #         config,
    #         prompt=args.prompt,
    #     ).cuda()
    if args.use_samus and USE_SAMUS:  # 修改为检查命令行参数
        print("使用 SAMUS 模型进行测试")
        net = SAMUSAdapter(
            config,
            prompt=args.prompt,
        ).cuda()
    else:
        print("使用 ViT_omni 模型进行测试")
        net = ViT_omni(
            config,
            prompt=args.prompt,
        ).cuda()
    net.load_from(config)
    
    # 其余代码保持不变...

    snapshot = os.path.join(args.output_dir, 'best_model.pth')
    if not os.path.exists(snapshot):
        snapshot = snapshot.replace('best_model', 'epoch_'+str(args.max_epochs-1))

    device = torch.device("cuda")
    model = net.to(device=device)
    model = torch.nn.SyncBatchNorm.convert_sync_batchnorm(model)
    torch.distributed.init_process_group(backend="nccl", init_method='env://', world_size=1, rank=0)
    model = torch.nn.parallel.DistributedDataParallel(model, find_unused_parameters=True)

    import copy
    pretrained_dict = torch.load(snapshot, map_location=device)
    full_dict = copy.deepcopy(pretrained_dict)
    for k, v in pretrained_dict.items():
        if "module." not in k:
            full_dict["module."+k] = v
            del full_dict[k]

    msg = model.load_state_dict(full_dict)

    print("self trained swin unet", msg)
    snapshot_name = snapshot.split('/')[-1]

    logging.basicConfig(filename=args.output_dir+"/"+"test_result.txt", level=logging.INFO,
                        format='[%(asctime)s.%(msecs)03d] %(message)s', datefmt='%H:%M:%S')
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    logging.info(str(args))
    logging.info(snapshot_name)

    if args.is_saveout:
        args.test_save_dir = os.path.join(args.output_dir, "predictions")
        test_save_path = args.test_save_dir
        os.makedirs(test_save_path, exist_ok=True)
    else:
        test_save_path = None
    inference(args, net, test_save_path)
