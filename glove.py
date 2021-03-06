from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
import numpy as np

import os
import sys
import threading
import time
from tensorflow.python.client import timeline

flags = tf.app.flags

flags.DEFINE_string("save_path", None, "Directory to write the model and "
                                       "training summaries.")
flags.DEFINE_string("train_data", None, "Training text file. Cooccur_matrix File")
flags.DEFINE_string("eval_data", None, "File to eval")
flags.DEFINE_string("vocab_data", None, "vocab data")
flags.DEFINE_integer("embedding_size", 200, "The embedding dimension size.")
flags.DEFINE_integer(
    "epochs_to_train", 15,
    "Number of epochs to train. Each epoch processes the training data once "
    "completely.")
flags.DEFINE_float("learning_rate", 0.2, "Initial learning rate.")
flags.DEFINE_integer("batch_size", 16,
                     "Number of training examples processed per step "
                     "(size of a minibatch).")
flags.DEFINE_integer("concurrent_steps", 12,
                     "The number of concurrent training steps.")
flags.DEFINE_integer("vocab_size", None, "The vocab size.")
flags.DEFINE_integer("matrix_size", None, "Matrix Size.")
flags.DEFINE_integer("load_data_per_time", 100, "load number of lines per read")
flags.DEFINE_boolean(
    "restore_model", False,
    "If true, load model from load path")
flags.DEFINE_string("load_path", None, "Load model from the path")

FLAGS = flags.FLAGS


class GloVe(object):
    def __init__(self, options, session):
        self._save_path = options.save_path
        self._train_data = options.train_data
        self._embedding_size = options.embedding_size
        self._learning_rate = options.learning_rate
        self._batch_size = options.batch_size
        self._concurrent_steps = options.concurrent_steps
        self._learning_rate = options.learning_rate
        self._vocab_size = options.vocab_size
        self._num_lines = options.matrix_size
        self._num_epochs = options.epochs_to_train
        self._eval_data = options.eval_data
        self._vocab_data = options.vocab_data
        self._restore_model = options.restore_model
        self._load_path = options.load_path
        self.dictionary = dict()
        self.reverse_dictionary = dict()
        self._x_max = 100
        self._alpha = 0.75
        self._session = session
        self.load_vocab()
        self.build_train_graph()
        self.build_eval_graph()
        self.saver = tf.train.Saver()

    def load_vocab(self):
        with open(self._vocab_data, "rb") as f:
            for line in f:
                line_split = line.split()
                key = line_split[0]
                value = int(line_split[1])
                self.dictionary[key] = value
                self.reverse_dictionary[value] = key
            self._vocab_size = len(self.dictionary)
        print("Vocab size is %d" % self._vocab_size)

    def read_analogies(self):
        """Reads through the analogy question file.

        Returns:
          questions: a [n, 4] numpy array containing the analogy question's
                     word ids.
          questions_skipped: questions skipped due to unknown words.
        """
        questions = []
        questions_skipped = 0
        with open(self._eval_data, "rb") as analogy_f:
            for line in analogy_f:
                if line.startswith(b":"):  # Skip comments.
                    continue
                words = line.strip().lower().split(b" ")
                ids = [self.dictionary.get(w.strip()) for w in words]
                if None in ids or len(ids) != 4:
                    questions_skipped += 1
                else:
                    questions.append(np.array(ids))
        print("Eval analogy file: ", self._eval_data)
        print("Questions: ", len(questions))
        print("Skipped: ", questions_skipped)
        self._analogy_questions = np.array(questions, dtype=np.int32)

    def build_train_graph(self):
        self.target, self.context, self.label = self.read_data_from_csv()
        # self.target = tf.placeholder(tf.int32, shape=[self._batch_size], name="target")
        # self.context = tf.placeholder(tf.int32, shape=[self._batch_size], name="context")
        # self.label = tf.placeholder(tf.float32, shape=[self._batch_size], name="label")
        alpha = tf.constant(self._alpha, dtype=tf.float32)
        x_max = tf.constant(self._x_max, dtype=tf.float32)

        # target_emb_w [vocab_size,emb_dim]
        self.target_emb_w = tf.Variable(
            tf.random_uniform(
                [self._vocab_size, self._embedding_size], -0.5 / self._embedding_size, 0.5 / self._embedding_size),
            name="target_emb_w")

        # context_emb_w [vocab_size,emb_dim]
        self.context_emb_w = tf.Variable(
            tf.random_uniform(
                [self._vocab_size, self._embedding_size], -0.5 / self._embedding_size, 0.5 / self._embedding_size),
            name="context_emb_w")

        # target_emb_b [vocab_size]
        self.target_emb_b = tf.Variable(tf.zeros([self._vocab_size]), name="target_emb_w")
        # context_emb_b [vocab_size]
        self.context_emb_b = tf.Variable(tf.zeros([self._vocab_size]), name="context_emb_w")

        # target_w [batch_size,emb_size]
        target_w = tf.nn.embedding_lookup(self.target_emb_w, self.target)
        # context_w [batch_size,emb_size]
        context_w = tf.nn.embedding_lookup(self.context_emb_w, self.context)

        # target_b [batch_size]
        target_b = tf.nn.embedding_lookup(self.target_emb_b, self.target)
        # context_b [bath_size]
        context_b = tf.nn.embedding_lookup(self.context_emb_b, self.context)

        diff = tf.square(
            tf.reduce_sum(tf.multiply(target_w, context_w), axis=1) - target_b - context_b - tf.log(
                tf.cast(self.label, tf.float32))
        )

        fdiff = tf.minimum(
            diff,
            tf.pow(tf.cast(self.label, tf.float32) / x_max, alpha) * diff
        )

        loss = tf.reduce_mean(tf.multiply(diff, fdiff))

        self._loss = loss

        self.global_step = tf.Variable(0, name="global_step")


        self._optimizer = tf.train.AdagradOptimizer(self._learning_rate).minimize(loss, global_step=self.global_step)

    def build_eval_graph(self):
        """Build the eval graph."""
        # Eval graph

        # Each analogy task is to predict the 4th word (d) given three
        # words: a, b, c.  E.g., a=italy, b=rome, c=france, we should
        # predict d=paris.

        # The eval feeds three vectors of word ids for a, b, c, each of
        # which is of size N, where N is the number of analogies we want to
        # evaluate in one batch.
        analogy_a = tf.placeholder(dtype=tf.int32)  # [N]
        analogy_b = tf.placeholder(dtype=tf.int32)  # [N]
        analogy_c = tf.placeholder(dtype=tf.int32)  # [N]

        # Normalized word embeddings of shape [vocab_size, emb_dim].
        emb = self.target_emb_w + self.context_emb_w
        # emb = self.target_emb_w
        nemb = tf.nn.l2_normalize(emb, 1)

        # Each row of a_emb, b_emb, c_emb is a word's embedding vector.
        # They all have the shape [N, emb_dim]
        a_emb = tf.gather(nemb, analogy_a)  # a's embs
        b_emb = tf.gather(nemb, analogy_b)  # b's embs
        c_emb = tf.gather(nemb, analogy_c)  # c's embs

        # We expect that d's embedding vectors on the unit hyper-sphere is
        # near: c_emb + (b_emb - a_emb), which has the shape [N, emb_dim].
        target = c_emb + (b_emb - a_emb)

        # Compute cosine distance between each pair of target and vocab.
        # dist has shape [N, vocab_size].
        dist = tf.matmul(target, nemb, transpose_b=True)

        # For each question (row in dist), find the top 4 words.
        _, pred_idx = tf.nn.top_k(dist, 4)

        # Nodes for computing neighbors for a given word according to
        # their cosine distance.
        nearby_word = tf.placeholder(dtype=tf.int32)  # word id
        nearby_emb = tf.gather(nemb, nearby_word)
        nearby_dist = tf.matmul(nearby_emb, nemb, transpose_b=True)
        nearby_val, nearby_idx = tf.nn.top_k(nearby_dist,
                                             min(1000, self._vocab_size))

        # Nodes in the construct graph which are used by training and
        # evaluation to run/feed/fetch.
        self._analogy_a = analogy_a
        self._analogy_b = analogy_b
        self._analogy_c = analogy_c
        self._analogy_pred_idx = pred_idx
        self._nearby_word = nearby_word
        self._nearby_val = nearby_val
        self._nearby_idx = nearby_idx

    def _predict(self, analogy):
        """Predict the top 4 answers for analogy questions."""
        idx, = self._session.run([self._analogy_pred_idx], {
            self._analogy_a: analogy[:, 0],
            self._analogy_b: analogy[:, 1],
            self._analogy_c: analogy[:, 2]
        })
        return idx

    def eval(self):
        """Evaluate analogy questions and reports accuracy."""

        # How many questions we get right at precision@1.
        correct = 0

        try:
            total = self._analogy_questions.shape[0]
        except AttributeError as e:
            raise AttributeError("Need to read analogy questions.")

        start = 0
        while start < total:
            limit = start + 2500
            sub = self._analogy_questions[start:limit, :]
            idx = self._predict(sub)
            start = limit
            for question in xrange(sub.shape[0]):
                for j in xrange(4):
                    if idx[question, j] == sub[question, 3]:
                        # Bingo! We predicted correctly. E.g., [italy, rome, france, paris].
                        correct += 1
                        break
                    elif idx[question, j] in sub[question, :3]:
                        # We need to skip words already in the question.
                        continue
                    else:
                        # The correct label is not the precision@1
                        break
        print()
        print("Eval %4d/%d accuracy = %4.1f%%" % (correct, total,
                                                  correct * 100.0 / total))

    def generate_batch(self, i):
        target = np.ndarray(shape=self._batch_size, dtype=np.int32)
        context = np.ndarray(shape=self._batch_size, dtype=np.int32)
        label = np.ndarray(shape=self._batch_size, dtype=np.int32)
        with open(self._train_data, "r") as f:
            for _ in xrange(0, i * self._batch_size):
                f.readline()
            line_index = 0
            for _ in xrange(i * self._batch_size, (i + 1) * self._batch_size):
                line = f.readline()

                if not line:
                    break
                line = line.split()
                target[line_index] = int(line[0])
                context[line_index] = int(line[1])
                label[line_index] = int(line[2])
                line_index += 1
        return target, context, label

    def read_data_from_csv(self):
        filename_queue = tf.train.string_input_producer([self._train_data])
        reader = tf.TextLineReader()
        key, value = reader.read_up_to(filename_queue, num_records=self._batch_size)
        record_defaults = [[0], [0], [0.0]]
        data = tf.decode_csv(value, record_defaults=record_defaults)
        target = data[0]
        context = data[1]
        label = data[2]
        # return target,context,label
        min_after_dequeue = 10000
        capacity = min_after_dequeue + 3 * self._batch_size
        target_batch, context_batch, label_batch = tf.train.shuffle_batch(
            [target, context, label], batch_size=self._batch_size, capacity=capacity, enqueue_many=True,
            min_after_dequeue=min_after_dequeue)
        return target_batch, context_batch, label_batch

    def read_data(self):
        filename_queue = tf.train.string_input_producer([self._train_data], num_epochs=self._num_epochs)
        reader = tf.TFRecordReader()
        _, serialized_example = reader.read_up_to(filename_queue, self._batch_size)
        features = tf.parse_example(
            serialized_example,
            # Defaults are not specified since both keys are required.
            features={
                'target': tf.FixedLenFeature([], dtype=tf.int64),
                'context': tf.FixedLenFeature([], dtype=tf.int64),
                'label': tf.FixedLenFeature([], dtype=tf.int64)
            })

        target = features['target']
        context = features['context']
        label = features['label']

        min_after_dequeue = 10000
        capacity = min_after_dequeue + 3 * self._batch_size
        target_batch, context_batch, label_batch = tf.train.shuffle_batch(
            [target, context, label], batch_size=self._batch_size, capacity=capacity,
            min_after_dequeue=min_after_dequeue, num_threads=self._concurrent_steps, enqueue_many=True)
        return target_batch, context_batch, label_batch

    def init(self):
        if self._restore_model:
            self.saver.restore(self._session, self._load_path)
        else:
            init_op = tf.group(tf.global_variables_initializer(),
                               tf.local_variables_initializer())
            self._session.run(init_op)
        print('Initialized')

    def run(self):
        average_loss = 0
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=self._session, coord=coord)
        # options = tf.RunOptions(trace_level=tf.RunOptions.FULL_TRACE)
        # run_metadata = tf.RunMetadata()
        try:
            while not coord.should_stop():
                start_time = time.time()

                _, loss_val,step = self._session.run(
                    [self._optimizer, self._loss, self.global_step]
                    #    , options=options, run_metadata=run_metadata
                )
                if np.isnan(loss_val):
                    print("current loss IS NaN. This should never happen :)")
                    sys.exit(1)

                duration = time.time() - start_time

                average_loss += loss_val
                if step % 200 == 0:
                    if step > 0:
                        average_loss /= 200
                        # fetched_timeline = timeline.Timeline(run_metadata.step_stats)
                        # chrome_trace = fetched_timeline.generate_chrome_trace_format()
                        # with open('timeline_09.json', 'w') as f:
                        #     f.write(chrome_trace)
                        # The average loss is an estimate of the loss over the last 2000 batches.
                        print('Step: %d Avg_loss: %f (%.3f sec)\r' % (step, average_loss, duration), end="")
                        sys.stdout.flush()
                        average_loss = 0
                if step % 100000 == 0:
                    if step > 0:
                        self.eval()
                        self.saver.save(self._session, os.path.join(self._save_path, "model.ckpt"), global_step=step)

        except tf.errors.OutOfRangeError:
            print('Done training for %d epochs, %d steps.' % (self._num_epochs, step))
        finally:
            # When done, ask the threads to stop.
            coord.request_stop()

        coord.request_stop()
        coord.join(threads)


def main(_):
    if not FLAGS.save_path or not FLAGS.train_data or not FLAGS.eval_data or not FLAGS.vocab_data:
        print("--save_path --train_data --eval_data --vocab_data must be specified.")
        sys.exit(1)
    if FLAGS.restore_model and not FLAGS.load_path:
        print("--load_path must be specified.")
        sys.exit(1)

    with tf.Graph().as_default(), tf.Session() as session:
        model = GloVe(FLAGS, session)
        model.read_analogies()
        model.init()
        model.run()


if __name__ == "__main__":
    tf.app.run()
