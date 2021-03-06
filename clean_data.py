import tensorflow as tf
import numpy as np
import collections
import argparse
import os
import sys
import json

parser = argparse.ArgumentParser()

parser.add_argument("--save_path", type=str, default=None, help="Directory to write cleaned data")
parser.add_argument("--data_file", type=str, default=None, help="File of data to be cleaned")
parser.add_argument("--window_size", type=int, default=5,
                    help="The number of words to predict to the left and right "
                         "of the target word.")
parser.add_argument("--min_count", type=int, default=5,
                    help="The minimum number of word occurrences for it to be "
                         "included in the vocabulary.")
parser.add_argument("--subsample", type=float, default=1e-3,
                    help="Subsample threshold for word occurrence. Words that appear "
                         "with higher frequency will be randomly down-sampled. Set "
                         "to 0 to disable.")
args = parser.parse_args()
print args


class CleanData(object):
    def __init__(self, options):
        self._save_path = options.save_path
        self._data_file = options.data_file
        self._min_count = options.min_count
        self._subsample = options.subsample
        self._vocab_size = 0
        self._window_size = options.window_size
        self.dictionary = dict()
        self.reverse_dictionary = dict()

        if not self._data_file or not self._save_path:
            print("--save_path and --data_file must be specified.")
            sys.exit(1)

    def read_data(self):
        with open(self._data_file, "r") as f:
            data = f.read().split()
            print("data file contains %d words" % len(data))
        return data

    def build_dataset(self):
        data = collections.Counter(self.read_data()).most_common()
        end_index = 0
        for index, item in enumerate(reversed(data)):
            if item[1] >= self._min_count:
                end_index = index
                break
        data = data[0:-end_index]
        self._vocab_size = len(data)
        print("vocab size is %d." % self._vocab_size)

        for word, _ in data:
            self.dictionary[word] = len(self.dictionary)
        self.reverse_dictionary = dict(zip(self.dictionary.values(), self.dictionary.keys()))

        with open(os.path.join(self._save_path, "vocab.txt"), "w") as f:
            for key in self.dictionary:
                f.write("%s %d\n" % (key, self.dictionary[key]))
            print("Write vocab into %s." % os.path.join(self._save_path, "vocab.txt"))
        return data

    def update_coocur_line(self, line):
        window = self._window_size
        result = []
        line = line.split()
        for index, word in enumerate(line):
            if word in self.dictionary:
                target_index = self.dictionary[word]
            else:
                continue
            for i in range(max(0, index - window), min(len(line), index + window)):
                if i == index:
                    continue
                dist = abs(index - i)
                if line[i] in self.dictionary:
                    context_index = self.dictionary[line[i]]
                    result.append([target_index, context_index, 1.0/dist])
                else:
                    continue
        return result

    def save_as_csv(self, cooccur_matrix):
        line_number = 0
        with open(os.path.join(self._save_path, "cooccur_matrix.csv"), "w") as output:
            for i in xrange(self._vocab_size):
                for j in xrange(self._vocab_size):
                    key = str(i) + "-" + str(j)
                    if key in cooccur_matrix:
                        line_number += 1
                        output.write("%d,%d,%f\n" % (i, j, cooccur_matrix[key]))
                        if line_number % 10000 == 0:
                            print("Saved %d lines" % line_number)

        print("Save cooccur matrix into %s" % (os.path.join(self._save_path, "cooccur_matrix.csv")))

    def save_as_tfrecord(self, cooccur_matrix):
        line_number = 0
        with tf.python_io.TFRecordWriter(os.path.join(self._save_path, "cooccur_matrix.tfrecords")) as output:
            for i in xrange(self._vocab_size):
                for j in xrange(self._vocab_size):
                    key = str(i) + "-" + str(j)
                    if key in cooccur_matrix:
                        line_number += 1
                        example = tf.train.Example(features=tf.train.Features(feature={
                            'target': tf.train.Feature(int64_list=tf.train.Int64List(value=[i])),
                            'context': tf.train.Feature(int64_list=tf.train.Int64List(value=[j])),
                            'label': tf.train.Feature(int64_list=tf.train.Float64List(value=[cooccur_matrix[key]]))}))
                        output.write(example.SerializeToString())
                        line_number += 1
                        if line_number % 10000 == 0:
                            print("Saved %d lines" % line_number)

        print("Save cooccur matrix of size %d into %s" % (line_number , os.path.join(self._save_path, "cooccur_matrix.tfrecords")))

    def build_cooccur(self):
        cooccur_matrix = dict()
        line_number = 0
        with open(self._data_file, "r") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                line = self.update_coocur_line(line)
                for target_word, context_word, dist in line:
                    key = str(target_word) + "-" + str(context_word)
                    if key in cooccur_matrix:
                        cooccur_matrix[key] += dist
                    else:
                        cooccur_matrix[key] = dist
                if line_number % 1000 == 0:
                    print("Processed %d lines" % line_number)
                line_number += 1

        print("Finish processed files")
        self.save_as_csv(cooccur_matrix)

    def clean(self):
        self.build_dataset()
        self.build_cooccur()


if __name__ == "__main__":
    clean_data = CleanData(args)
    clean_data.clean()
