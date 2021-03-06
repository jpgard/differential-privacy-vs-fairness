import torch
import torchvision
from torchvision import transforms
import pandas as pd
import os
import numpy as np
from skimage import io
from torchvision.datasets.folder import default_loader
import glob
import re
import time
from os import path as osp

LFW_FILENAME_REGEX = re.compile("(\D+)_(\d{4})\.jpg")

LABEL_COLNAME = "label"
ATTR_COLNAME = "attr"


def extract_person_from_filename(x):
    """Helper function to extract the person name from a LFW filename."""
    res = re.match(LFW_FILENAME_REGEX, osp.basename(x))
    try:
        return res.group(1)
    except AttributeError:
        return None


def extract_imagenum_from_filename(x):
    """Helper function to extract the image number from a LFW filename."""
    res = re.match(LFW_FILENAME_REGEX, osp.basename(x))
    try:
        return res.group(2)
    except AttributeError:
        return None


def apply_thresh(df, colname, thresh: float, use_abs=True):
    """Apply thresh to df[colname] to filter rows, optionally applying abs() first."""
    if use_abs:
        return df[abs(df[colname]) >= thresh].reset_index().drop(columns='index')
    else:
        return df[df[colname] >= thresh].reset_index().drop(columns='index')


def get_anno_df(root_dir, partition, label_colname, label_threshold=None):
    """Fetch the dataframe of annotations and apply some preprocessing."""
    anno_fp = osp.join(root_dir, "lfw_attributes_cleaned.txt")

    train_fp = osp.join(root_dir, "peopleDevTrain.txt")
    test_fp = osp.join(root_dir, "peopleDevTest.txt")
    anno_df = pd.read_csv(anno_fp, delimiter="\t")
    if label_threshold:
        anno_df = apply_thresh(anno_df, label_colname, label_threshold)
    anno_df['imagenum_str'] = anno_df['imagenum'].apply(lambda x: f'{x:04}')
    anno_df['person'] = anno_df['person'].apply(lambda x: x.replace(" ", "_"))
    anno_df["img_basepath"] = (anno_df['person'] + '/' + anno_df['person'] + '_'
                               + anno_df['imagenum_str'] + '.jpg')
    anno_df["Mouth_Open"] = 1 - anno_df["Mouth_Closed"]

    # Subset to the correct partition
    if partition == 'train':
        partition_ids = pd.read_csv(train_fp, delimiter="\t", skiprows=0)
    elif partition == 'test':
        partition_ids = pd.read_csv(test_fp, delimiter="\t", skiprows=0)
    else:
        raise ValueError
    partition_idx = anno_df['person'].isin(partition_ids.index)
    df_out = anno_df[partition_idx].reset_index().drop(columns='index')
    return df_out


def make_lfw_file_pattern(dirname):
    return osp.join(dirname, "*/*.jpg")


def get_lfw_transforms(partition, normalize:bool=True):
    mu_data = [0.463666, 0.390829, 0.339801]
    std_data = [0.282721, 0.253934, 0.247486]
    im_size = [80, 80]
    crop_size = [64, 64]

    resize = transforms.Resize(im_size)
    rotate = transforms.RandomRotation(degrees=30)
    random_crop = transforms.RandomCrop(crop_size)  # Crops the training image
    flip_aug = transforms.RandomHorizontalFlip()
    normalize_aug = transforms.Normalize(mean=mu_data, std=std_data)
    center_crop = transforms.CenterCrop(crop_size)  # Crops the test image

    transform_train = transforms.Compose([resize,
                                          rotate, random_crop,
                                          flip_aug,
                                          transforms.ToTensor(),
                                          normalize_aug])
    transform_test = transforms.Compose([resize, center_crop,
                                         transforms.ToTensor(),
                                         normalize_aug])

    transform_test_unnormalized = transforms.Compose([resize, center_crop,
                                         transforms.ToTensor()])


    if partition == 'train':
        return transform_train
    elif partition == 'test':
        if not normalize:
            return transform_test_unnormalized
        return transform_test
    else:
        raise ValueError("Invalid partition {}".format(partition))


class LFWDataset(torch.utils.data.Dataset):
    """LFW Dataset."""

    def __init__(self, root_dir, target_colname, attribute_colname,
                 label_threshold,
                 transform=None,
                 partition='train', image_subdirectory="lfw-deepfunneled"
                 ):
        """
        Args:
            attr_file (string): Path to the file with annotations.
            root_dir (string): Directory with all the images.
            transform (callable, optional): Optional transform to be applied
                on a sample.
        """
        self.anno = get_anno_df(root_dir, partition, target_colname, label_threshold)
        self.root_dir = root_dir
        self.transform = transform
        self.loader = default_loader
        self.target_colname = target_colname
        self.attribute_colname = attribute_colname
        self.threshold=label_threshold

        self.image_subdirectory = image_subdirectory

    def __len__(self):
        return len(self.anno)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()
        img_name = os.path.join(self.root_dir, self.image_subdirectory,
                                self.anno['img_basepath'].iloc[idx])
        image = self.loader(img_name)
        soft_labels = self.anno.iloc[idx, :][self.target_colname]
        # Cast labels to 1 if > 0, and zero otherwise
        hard_labels = (soft_labels > 0).astype(int)
        label_numpy = np.array(hard_labels)
        label = torch.from_numpy(label_numpy)

        if self.transform:
            image = self.transform(image)
        sample = (image, idx, label)
        return sample

    def get_attribute_annotations(self, idxs):
        idx_annos = self.anno.iloc[idxs, :][self.attribute_colname]
        def _revalue_fn(x):
            if x < -self.threshold:
                return 0
            if x > self.threshold:
                return 1
            else:
                return np.nan
        idx_annos = idx_annos.apply(_revalue_fn)
        return idx_annos
